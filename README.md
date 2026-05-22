# chisel_register_converter

Convert Chisel Bundle definitions into packed 32-bit SystemRDL registers and generate Chisel binding statements between bundle fields and register fields.

## Modes

The tool has two operation modes:

- `--rdl`: Parse Chisel bundles and generate SystemRDL.
- `--binding`: Generate assignment statements between bundle fields and register fields.

## 1) RDL Mode (`--rdl`)

Generate packed registers from one or more bundle classes.

Required inputs:

- `--files`: Chisel source file list.
- `--bundles`: Bundle names to convert.
- `--output`: Output RDL file.

Optional:

- `--mapping-output`: Path to JSON mapping file. If omitted, defaults to `<output_stem>.mapping.json`.

Example:

```bash
python3 chisel_reg_converter.py \
	--rdl \
	--files sample.scala \
	--bundles tSAMPLE_CORE_CONFIG tSAMPLE_TX_DATA \
	--output regs.rdl
```

This generates:

- `regs.rdl`
- `regs.mapping.json`

## 2) Binding Mode (`--binding`)

Generate bundle/register assignments for a single bundle.

Required inputs:

- `--bundle`: Bundle name.
- `--mapping-input`: Mapping JSON produced by `--rdl`.
- `--output`: Output binding text file.
- One direction flag:
	- `--config`: `bundle.field := REG_field`
	- `--status`: `REG_field := bundle.field`

Optional:

- `--bundle-ref`: Name used on the bundle side in generated lines (default: `bundle`).

Config example:

```bash
python3 chisel_reg_converter.py \
	--binding \
	--bundle tSAMPLE_CORE_CONFIG \
	--config \
	--mapping-input regs.mapping.json \
	--output config.bindings.txt
```

Status example:

```bash
python3 chisel_reg_converter.py \
	--binding \
	--bundle tSAMPLE_CORE_CONFIG \
	--status \
	--mapping-input regs.mapping.json \
	--output status.bindings.txt
```

## Notes

- Fields wider than 32 bits are split into 32-bit chunks and bound with bit slices.
- Dynamic/unparseable widths are skipped with warnings.
- Hierarchical bundles are supported. Nested bundles are recursively flattened into registers under the selected parent bundle.
- Mapping entries preserve hierarchy in `bundle_path` (for example: `sample_core_config.core_id`) while register fields remain flat.