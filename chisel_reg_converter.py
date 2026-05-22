import argparse
import json
import re
import sys
import os


def parse_chisel_files(file_paths, target_bundles):
    """
    Parses Chisel files to extract fields and their widths for the target bundles.
    """
    bundles = {name: [] for name in target_bundles}

    # Regex to extract fields like: val name = Input(UInt(8.W)) or val name = Bool()
    # Matches: val <name> = <optional_io>( <type>(<optional_width>) )
    field_pattern = re.compile(
        r'val\s+(?P<name>\w+)\s*=\s*(?:(?:Input|Output|Flipped)\s*\()?.*?(?P<type>UInt|SInt|Bool)\s*\((?:\s*(?P<width>\d+)\s*\.\s*W\s*)?\)'
    )

    for file_path in file_paths:
        if not os.path.isfile(file_path):
            print(f"Error: File '{file_path}' not found.")
            continue

        with open(file_path, 'r') as f:
            content = f.read()

        # Split by "class " to isolate bundle definitions roughly
        blocks = content.split("class ")
        for block in blocks:
            if not block.strip():
                continue

            lines = block.split('\n')
            first_line = lines[0]

            # Check if this class extends Bundle
            match = re.match(r'(?P<name>\w+).*?extends\s+(?:.*)?Bundle', first_line)
            if match:
                b_name = match.group('name')
                if b_name in target_bundles:
                    # Parse fields in this bundle block
                    for f_match in field_pattern.finditer(block):
                        f_name = f_match.group('name')
                        f_type = f_match.group('type')
                        f_width_str = f_match.group('width')

                        if f_type == 'Bool':
                            f_width = 1
                        elif f_width_str:
                            f_width = int(f_width_str)
                        else:
                            # Skip fields with dynamically calculated widths (e.g. .getWidth.W)
                            print(
                                f"Warning: Field '{f_name}' in '{b_name}' has a dynamic or unparseable width. Skipping.")
                            continue

                        bundles[b_name].append({'name': f_name, 'width': f_width})

    return bundles


def build_bundle_layout(bundle_name, fields):
    """
    Build packed 32-bit register layout and binding map for a single bundle.
    """
    registers = []
    bindings = []

    reg_idx = 0
    current_bit = 0
    current_fields = []

    def flush_reg():
        nonlocal reg_idx, current_bit, current_fields
        if not current_fields:
            return

        reg_name = f"{bundle_name}_{reg_idx}"
        reg_fields = []
        for f in current_fields:
            reg_fields.append({
                'name': f['name'],
                'lsb': f['lsb'],
                'msb': f['msb']
            })

        registers.append({
            'name': reg_name,
            'fields': reg_fields
        })

        reg_idx += 1
        current_bit = 0
        current_fields = []

    for f in fields:
        w = f['width']
        f_name = f['name']

        if w <= 32:
            if current_bit + w > 32:
                flush_reg()

            lsb = current_bit
            msb = current_bit + w - 1
            current_fields.append({
                'name': f_name,
                'lsb': lsb,
                'msb': msb
            })

            target_reg_name = f"{bundle_name}_{reg_idx}"
            bindings.append({
                'bundle_field': f_name,
                'bundle_msb': None,
                'bundle_lsb': None,
                'register': target_reg_name,
                'register_field': f_name,
                'register_signal': f"{target_reg_name}_{f_name}",
                'width': w
            })

            current_bit += w
        else:
            if current_fields:
                flush_reg()

            processed = 0
            while processed < w:
                chunk_w = min(32, w - processed)
                lsb_in_field = processed
                msb_in_field = processed + chunk_w - 1
                chunk_name = f"{f_name}_{msb_in_field}_{lsb_in_field}"

                current_fields.append({
                    'name': chunk_name,
                    'lsb': 0,
                    'msb': chunk_w - 1
                })

                target_reg_name = f"{bundle_name}_{reg_idx}"
                bindings.append({
                    'bundle_field': f_name,
                    'bundle_msb': msb_in_field,
                    'bundle_lsb': lsb_in_field,
                    'register': target_reg_name,
                    'register_field': chunk_name,
                    'register_signal': f"{target_reg_name}_{chunk_name}",
                    'width': chunk_w
                })

                flush_reg()
                processed += chunk_w

    flush_reg()
    return registers, bindings


def generate_rdl(bundles, output_file, mapping_output_file=None):
    """
    Packs the extracted fields into 32-bit registers and writes an RDL file.
    Fields > 32b are split into dedicated, unshared registers.
    """
    rdl_lines = []
    rdl_lines.append("addrmap ChiselRegMap {")
    rdl_lines.append('    name = "Chisel Generated Register Map";')
    rdl_lines.append("    default regwidth = 32;")
    rdl_lines.append("    default sw = rw;")
    rdl_lines.append("    default hw = r;")
    rdl_lines.append("")

    offset = 0x0
    mapping = {'bundles': {}}

    for b_name, fields in bundles.items():
        if not fields:
            print(f"Warning: No valid fields found for bundle '{b_name}'.")
            continue

        registers, bindings = build_bundle_layout(b_name, fields)
        mapping['bundles'][b_name] = bindings

        for reg in registers:
            reg_name = reg['name']
            rdl_lines.append(f"    reg {reg_name}_t {{")
            rdl_lines.append(f'        name = "{reg_name}";')

            for f in reg['fields']:
                f_name = f['name']
                lsb = f['lsb']
                msb = f['msb']
                if lsb == msb:
                    rdl_lines.append(f"        field {{}} {f_name}[{lsb}];")
                else:
                    rdl_lines.append(f"        field {{}} {f_name}[{msb}:{lsb}];")

            rdl_lines.append("    };")
            rdl_lines.append(f"    {reg_name}_t {reg_name} @ {hex(offset)};")
            rdl_lines.append("")
            offset += 4

    rdl_lines.append("};")

    # Write to file
    with open(output_file, 'w') as f:
        f.write("\n".join(rdl_lines) + "\n")
    print(f"Successfully generated RDL file: {output_file}")

    if mapping_output_file:
        with open(mapping_output_file, 'w') as f:
            json.dump(mapping, f, indent=2)
            f.write("\n")
        print(f"Successfully generated mapping file: {mapping_output_file}")


def derive_default_mapping_path(rdl_output_path):
    base, _ = os.path.splitext(rdl_output_path)
    return f"{base}.mapping.json"


def load_bundle_bindings_from_mapping(mapping_file, bundle_name):
    with open(mapping_file, 'r') as f:
        mapping = json.load(f)

    bundles = mapping.get('bundles', {})
    if bundle_name not in bundles:
        print(f"Error: Bundle '{bundle_name}' not found in mapping file '{mapping_file}'.")
        sys.exit(1)

    return bundles[bundle_name]


def build_bundle_bindings_from_chisel(chisel_file, bundle_name):
    bundles = parse_chisel_files([chisel_file], [bundle_name])
    fields = bundles.get(bundle_name, [])
    if not fields:
        print(f"Error: No valid fields found for bundle '{bundle_name}' in file '{chisel_file}'.")
        sys.exit(1)

    _, bindings = build_bundle_layout(bundle_name, fields)
    return bindings


def format_bundle_expr(bundle_ref, binding):
    field_name = binding['bundle_field']
    msb = binding.get('bundle_msb')
    lsb = binding.get('bundle_lsb')

    if msb is None or lsb is None:
        return f"{bundle_ref}.{field_name}"
    return f"{bundle_ref}.{field_name}({msb}, {lsb})"


def generate_binding_file(bindings, bundle_ref, is_config_binding, output_file):
    lines = []
    for binding in bindings:
        bundle_expr = format_bundle_expr(bundle_ref, binding)
        reg_expr = binding['register_signal']

        if is_config_binding:
            lines.append(f"{bundle_expr} := {reg_expr}")
        else:
            lines.append(f"{reg_expr} := {bundle_expr}")

    with open(output_file, 'w') as f:
        f.write("\n".join(lines) + "\n")

    print(f"Successfully generated binding file: {output_file}")


def main():
    parser = argparse.ArgumentParser(description="Convert Chisel Bundles to SystemRDL and generate bundle-register bindings.")

    mode_group = parser.add_mutually_exclusive_group(required=True)
    mode_group.add_argument('--rdl', action='store_true', help="RDL generation mode")
    mode_group.add_argument('--binding', action='store_true', help="Binding generation mode")

    parser.add_argument('-o', '--output', required=True, help="Output file path")

    # RDL mode inputs
    parser.add_argument('-f', '--files', nargs='+', help="List of Chisel (.scala) files to parse in --rdl mode")
    parser.add_argument('-b', '--bundles', nargs='+', help="List of Bundle names to convert in --rdl mode")
    parser.add_argument('--mapping-output', help="Optional mapping JSON output path in --rdl mode")

    # Binding mode inputs
    parser.add_argument('--file', help="Chisel (.scala) file containing the target bundle in --binding mode")
    parser.add_argument('--bundle', help="Bundle name to bind in --binding mode")
    parser.add_argument('--mapping-input', help="Optional mapping JSON input path in --binding mode")
    parser.add_argument('--bundle-ref', default='bundle', help="Reference name used in generated binding lines (default: bundle)")

    bind_type_group = parser.add_mutually_exclusive_group()
    bind_type_group.add_argument('--config', action='store_true', help="Generate config bindings: bundle.field := REG_field")
    bind_type_group.add_argument('--status', action='store_true', help="Generate status bindings: REG_field := bundle.field")

    args = parser.parse_args()

    if args.rdl:
        if not args.files or not args.bundles:
            print("Error: --rdl mode requires --files and --bundles.")
            sys.exit(1)

        mapping_output = args.mapping_output or derive_default_mapping_path(args.output)

        print("Parsing Chisel files...")
        bundles = parse_chisel_files(args.files, args.bundles)

        print("Packing fields and generating RDL...")
        generate_rdl(bundles, args.output, mapping_output)
        return

    if args.binding:
        if not args.file or not args.bundle:
            print("Error: --binding mode requires --file and --bundle.")
            sys.exit(1)
        if not args.config and not args.status:
            print("Error: --binding mode requires one of --config or --status.")
            sys.exit(1)

        if args.mapping_input:
            print(f"Loading bindings from mapping file: {args.mapping_input}")
            bindings = load_bundle_bindings_from_mapping(args.mapping_input, args.bundle)
        else:
            print("No mapping file provided. Deriving bindings from Chisel bundle layout...")
            bindings = build_bundle_bindings_from_chisel(args.file, args.bundle)

        generate_binding_file(bindings, args.bundle_ref, args.config, args.output)
        return

    print("Error: No mode selected.")
    sys.exit(1)


if __name__ == "__main__":
    main()
    