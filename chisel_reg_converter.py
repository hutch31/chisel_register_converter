"""
chisel_register_converter

Convert Chisel Bundle definitions into packed 32-bit SystemRDL registers and
generate Chisel binding statements between bundle fields and register fields.

Modes
-----
The tool has two operation modes:

- --rdl: Parse Chisel bundles and generate SystemRDL.
- --binding: Generate assignment statements between bundle fields and register
    fields.

1) RDL Mode (--rdl)
-------------------
Generate packed registers from one or more bundle classes.

Required inputs:
- --files: Chisel source file list.
- --bundles: Bundle names to convert.
- --output: Output RDL file.

Optional:
- --mapping-output: Path to JSON mapping file. If omitted, defaults to
    <output_stem>.mapping.json.

Example:
        python3 chisel_reg_converter.py \
            --rdl \
            --files sample.scala \
            --bundles tSAMPLE_CORE_CONFIG tSAMPLE_TX_DATA \
            --output regs.rdl

This generates:
- regs.rdl
- regs.mapping.json

2) Binding Mode (--binding)
---------------------------
Generate bundle/register assignments for a single bundle.

Required inputs:
- --bundle: Bundle name.
- --mapping-input: Mapping JSON produced by --rdl.
- --output: Output binding text file.
- One direction flag:
    - --config: bundle.field := REG_field
    - --status: REG_field := bundle.field

Optional:
- --bundle-ref: Name used on the bundle side in generated lines (default:
    bundle).

Config example:
        python3 chisel_reg_converter.py \
            --binding \
            --bundle tSAMPLE_CORE_CONFIG \
            --config \
            --mapping-input regs.mapping.json \
            --output config.bindings.txt

Status example:
        python3 chisel_reg_converter.py \
            --binding \
            --bundle tSAMPLE_CORE_CONFIG \
            --status \
            --mapping-input regs.mapping.json \
            --output status.bindings.txt

Notes
-----
- Fields wider than 32 bits are split into 32-bit chunks and bound with bit
    slices.
- Dynamic/unparseable widths are skipped with warnings.
"""

import argparse
import json
import re
import sys
import os


def unwrap_chisel_wrappers(expr):
    """
    Remove common Chisel directional wrappers like Input(...), Output(...), Flipped(...).
    """
    wrapped_pattern = re.compile(r'^\s*(Input|Output|Flipped)\s*\((.*)\)\s*$')
    unwrapped = expr.strip()

    while True:
        match = wrapped_pattern.match(unwrapped)
        if not match:
            break
        unwrapped = match.group(2).strip()

    return unwrapped


def parse_bundle_class_definitions(file_paths):
    """
    Parse all classes that extend Bundle and extract direct field definitions.

    Returns:
        dict[str, list[dict]]: {
            BundleName: [
                {'kind': 'primitive', 'name': <field>, 'width': <int>},
                {'kind': 'bundle', 'name': <field>, 'bundle_type': <BundleName>}
            ]
        }
    """
    bundle_defs = {}
    class_header_pattern = re.compile(r'^\s*class\s+(?P<name>\w+)\b.*?extends\s+(?:.*\.)?Bundle\b')
    field_pattern = re.compile(r'^\s*val\s+(?P<name>\w+)\s*=\s*(?P<expr>.+?)\s*$')
    static_width_pattern = re.compile(r'^(UInt|SInt)\s*\(\s*(?P<width>\d+)\s*\.\s*W\s*\)\s*$')
    dynamic_width_pattern = re.compile(r'^(UInt|SInt)\s*\(')
    bool_pattern = re.compile(r'^Bool\s*\(\s*\)\s*$')
    bundle_inst_pattern = re.compile(r'^new\s+(?P<type>[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\s*(?:\(\s*\))?\s*$')

    for file_path in file_paths:
        if not os.path.isfile(file_path):
            print(f"Error: File '{file_path}' not found.")
            continue

        with open(file_path, 'r') as f:
            lines = f.readlines()

        in_bundle = False
        current_bundle_name = None
        current_bundle_lines = []
        brace_depth = 0

        for line in lines:
            if not in_bundle:
                class_match = class_header_pattern.match(line)
                if not class_match:
                    continue

                in_bundle = True
                current_bundle_name = class_match.group('name')
                current_bundle_lines = []
                brace_depth = line.count('{') - line.count('}')

                # Skip classes without an explicit body block.
                if brace_depth <= 0:
                    in_bundle = False
                    current_bundle_name = None
                    current_bundle_lines = []
                    brace_depth = 0
                continue

            current_bundle_lines.append(line)
            brace_depth += line.count('{') - line.count('}')

            if brace_depth > 0:
                continue

            fields = []
            for raw_line in current_bundle_lines:
                line_wo_comment = raw_line.split('//', 1)[0].strip()
                if not line_wo_comment:
                    continue

                field_match = field_pattern.match(line_wo_comment)
                if not field_match:
                    continue

                f_name = field_match.group('name')
                expr = unwrap_chisel_wrappers(field_match.group('expr'))

                if bool_pattern.match(expr):
                    fields.append({'kind': 'primitive', 'name': f_name, 'width': 1})
                    continue

                width_match = static_width_pattern.match(expr)
                if width_match:
                    fields.append({'kind': 'primitive', 'name': f_name, 'width': int(width_match.group('width'))})
                    continue

                if dynamic_width_pattern.match(expr):
                    print(
                        f"Warning: Field '{f_name}' in '{current_bundle_name}' has a dynamic or unparseable width. Skipping.")
                    continue

                bundle_match = bundle_inst_pattern.match(expr)
                if bundle_match:
                    bundle_type = bundle_match.group('type').split('.')[-1]
                    fields.append({'kind': 'bundle', 'name': f_name, 'bundle_type': bundle_type})

            bundle_defs[current_bundle_name] = fields

            in_bundle = False
            current_bundle_name = None
            current_bundle_lines = []
            brace_depth = 0

    return bundle_defs


def flatten_bundle_fields(bundle_name, bundle_defs, path_prefix=None, stack=None):
    """
    Recursively flatten a bundle's primitive fields while preserving full hierarchy.
    """
    if path_prefix is None:
        path_prefix = []
    if stack is None:
        stack = []

    if bundle_name in stack:
        cycle_path = " -> ".join(stack + [bundle_name])
        print(f"Warning: Recursive bundle reference detected ({cycle_path}). Skipping nested expansion.")
        return []

    if bundle_name not in bundle_defs:
        print(f"Warning: Bundle type '{bundle_name}' not found. Skipping nested expansion.")
        return []

    flat_fields = []
    next_stack = stack + [bundle_name]

    for field in bundle_defs[bundle_name]:
        current_path = path_prefix + [field['name']]

        if field['kind'] == 'primitive':
            dotted_path = '.'.join(current_path)
            flat_fields.append({
                'name': '_'.join(current_path),
                'path': dotted_path,
                'width': field['width']
            })
            continue

        nested_bundle_type = field['bundle_type']
        flat_fields.extend(flatten_bundle_fields(nested_bundle_type, bundle_defs, current_path, next_stack))

    return flat_fields


def parse_chisel_files(file_paths, target_bundles):
    """
    Parses Chisel files and recursively flattens target bundle fields.
    """
    bundles = {}
    bundle_defs = parse_bundle_class_definitions(file_paths)

    for bundle_name in target_bundles:
        bundles[bundle_name] = flatten_bundle_fields(bundle_name, bundle_defs)

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
        f_path = f.get('path', f_name)

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
                'bundle_path': f_path,
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
                    'bundle_path': f_path,
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


def format_bundle_expr(bundle_ref, binding):
    field_name = binding.get('bundle_path') or binding['bundle_field']
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
    parser.add_argument('--bundle', help="Bundle name to bind in --binding mode")
    parser.add_argument('--mapping-input', help="Mapping JSON input path in --binding mode")
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
        if not args.bundle or not args.mapping_input:
            print("Error: --binding mode requires --bundle and --mapping-input.")
            sys.exit(1)
        if not args.config and not args.status:
            print("Error: --binding mode requires one of --config or --status.")
            sys.exit(1)

        print(f"Loading bindings from mapping file: {args.mapping_input}")
        bindings = load_bundle_bindings_from_mapping(args.mapping_input, args.bundle)

        generate_binding_file(bindings, args.bundle_ref, args.config, args.output)
        return

    print("Error: No mode selected.")
    sys.exit(1)


if __name__ == "__main__":
    main()
    