import argparse
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


def generate_rdl(bundles, output_file):
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

    for b_name, fields in bundles.items():
        if not fields:
            print(f"Warning: No valid fields found for bundle '{b_name}'.")
            continue

        reg_idx = 0
        current_bit = 0
        current_fields = []

        def flush_reg():
            nonlocal reg_idx, current_bit, current_fields, rdl_lines, offset
            if not current_fields:
                return

            reg_def_name = f"{b_name}_{reg_idx}"
            rdl_lines.append(f"    reg {reg_def_name}_t {{")
            rdl_lines.append(f'        name = "{reg_def_name}";')

            for f in current_fields:
                f_name = f['name']
                lsb = f['lsb']
                msb = f['msb']
                if lsb == msb:
                    rdl_lines.append(f"        field {{}} {f_name}[{lsb}];")
                else:
                    rdl_lines.append(f"        field {{}} {f_name}[{msb}:{lsb}];")

            rdl_lines.append("    };")
            # Instantiate the register
            rdl_lines.append(f"    {reg_def_name}_t {reg_def_name} @ {hex(offset)};")
            rdl_lines.append("")

            offset += 4
            reg_idx += 1
            current_bit = 0
            current_fields = []

        for f in fields:
            w = f['width']

            if w <= 32:
                # Standard packing logic for fields fitting within a single 32-bit register
                if current_bit + w > 32:
                    flush_reg()

                current_fields.append({
                    'name': f['name'],
                    'lsb': current_bit,
                    'msb': current_bit + w - 1
                })
                current_bit += w
            else:
                # Field is larger than 32b. Split it.
                # First, flush any existing accumulated normal fields to ensure exclusive access
                if current_fields:
                    flush_reg()

                processed = 0
                while processed < w:
                    chunk_w = min(32, w - processed)
                    lsb_in_field = processed
                    msb_in_field = processed + chunk_w - 1

                    # Construct split name suffix (e.g., big_field_63_32)
                    chunk_name = f"{f['name']}_{msb_in_field}_{lsb_in_field}"

                    current_fields.append({
                        'name': chunk_name,
                        'lsb': 0,
                        'msb': chunk_w - 1
                    })

                    # Flush immediately so each chunk occupies its own exclusive register
                    flush_reg()
                    processed += chunk_w

        # Flush any remaining normal fields left in the loop tail
        flush_reg()

    rdl_lines.append("};")

    # Write to file
    with open(output_file, 'w') as f:
        f.write("\n".join(rdl_lines) + "\n")
    print(f"Successfully generated RDL file: {output_file}")


def main():
    parser = argparse.ArgumentParser(description="Convert Chisel Bundles to 32b packed SystemRDL registers.")
    parser.add_argument('-f', '--files', nargs='+', required=True, help="List of Chisel (.scala) files to parse")
    parser.add_argument('-b', '--bundles', nargs='+', required=True, help="List of Bundle names to convert")
    parser.add_argument('-o', '--output', required=True, help="Output RDL file name")

    args = parser.parse_args()

    print("Parsing Chisel files...")
    bundles = parse_chisel_files(args.files, args.bundles)

    print("Packing fields and generating RDL...")
    generate_rdl(bundles, args.output)


if __name__ == "__main__":
    main()
    