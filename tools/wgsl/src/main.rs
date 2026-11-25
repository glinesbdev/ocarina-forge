use naga::valid::{Capabilities, ValidationFlags, Validator};
use std::env;
use std::fs;
use std::path::Path;

mod interface;

fn omit_unused_float_controls(bytes: &[u8]) -> Result<Vec<u8>, String> {
    const OP_EXTENSION: u32 = 10;
    const OP_EXECUTION_MODE: u32 = 16;
    const OP_EXECUTION_MODE_ID: u32 = 331;
    if bytes.len() < 20 || !bytes.len().is_multiple_of(4) {
        return Err("Truncated SPIR-V header or word.".into());
    }
    let mut body = &bytes[20..];
    let mut result = bytes[..20].to_vec();
    let mut float_controls_used = false;
    while !body.is_empty() {
        let instruction = u32::from_le_bytes(body[..4].try_into().unwrap());
        let size = (instruction >> 16) as usize * 4;
        if size == 0 || size > body.len() {
            return Err("Invalid SPIR-V instruction length.".into());
        }
        let (words, rest) = body.split_at(size);
        let opcode = instruction & 0xffff;
        if matches!(opcode, OP_EXECUTION_MODE | OP_EXECUTION_MODE_ID) && size >= 12 {
            let mode = u32::from_le_bytes(words[8..12].try_into().unwrap());
            float_controls_used |= (4459..=4463).contains(&mode);
        }
        if opcode != OP_EXTENSION
            || words[4..].split(|&b| b == 0).next() != Some(b"SPV_KHR_float_controls")
        {
            result.extend_from_slice(words);
        }
        body = rest;
    }
    if float_controls_used {
        // Execution-mode semantics must be preserved. Naga will report the
        // unsupported stage until there is a WGSL equivalent.
        return Ok(bytes.to_vec());
    }
    Ok(result)
}

fn validate(module: &naga::Module) -> Result<naga::valid::ModuleInfo, String> {
    // The first pass targets the core browser feature set. Optional features
    // need an explicit mapping to the adapter limits and enabled features.
    Validator::new(ValidationFlags::all(), Capabilities::empty())
        .validate(module)
        .map_err(|error| format!("{error:#?}"))
}

fn parse_wgsl(wgsl: &str) -> Result<naga::Module, String> {
    naga::front::wgsl::parse_str(wgsl)
        .map_err(|error| format!("WGSL reader: {}", error.emit_to_string(wgsl)))
}

fn validate_wgsl(wgsl: &str) -> Result<(), String> {
    let parsed = parse_wgsl(wgsl)?;
    validate(&parsed).map_err(|error| format!("WGSL validation: {error}"))?;
    Ok(())
}

fn convert(input: &Path, output: &Path) -> Result<(), String> {
    let bytes = fs::read(input).map_err(|error| error.to_string())?;
    // Longshot's Vulkan stage uses its own clip-space and binding conventions.
    // Preserve them here so this tool can measure conversion independently.
    let options = naga::front::spv::Options {
        adjust_coordinate_space: false,
        // Longshot declares SampledBuffer and ImageGatherExtended even in
        // stages that don't use them. Parse the real instructions and let the
        // core validator and WGSL round trip reject unsupported features.
        strict_capabilities: false,
        ..Default::default()
    };
    let bytes = omit_unused_float_controls(&bytes)?;
    let module = naga::front::spv::parse_u8_slice(&bytes, &options)
        .map_err(|error| format!("SPIR-V reader: {error}"))?;
    let info = validate(&module).map_err(|error| format!("SPIR-V validation: {error}"))?;
    let wgsl =
        naga::back::wgsl::write_string(&module, &info, naga::back::wgsl::WriterFlags::empty())
            .map_err(|error| format!("WGSL writer: {error}"))?;
    validate_wgsl(&wgsl)?;
    fs::write(output, wgsl).map_err(|error| format!("Writing WGSL: {error}"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn converts_a_core_compute_stage() {
        let module =
            naga::front::wgsl::parse_str("@compute @workgroup_size(1) fn main() {}").unwrap();
        let info = validate(&module).unwrap();
        let words = naga::back::spv::write_vec(&module, &info, &Default::default(), None).unwrap();
        let bytes: Vec<_> = words.into_iter().flat_map(u32::to_le_bytes).collect();
        let parsed = naga::front::spv::parse_u8_slice(
            &omit_unused_float_controls(&bytes).unwrap(),
            &Default::default(),
        )
        .unwrap();
        let info = validate(&parsed).unwrap();
        let wgsl =
            naga::back::wgsl::write_string(&parsed, &info, naga::back::wgsl::WriterFlags::empty())
                .unwrap();
        assert!(wgsl.contains("@compute @workgroup_size(1, 1, 1)"));
        validate(&naga::front::wgsl::parse_str(&wgsl).unwrap()).unwrap();
    }
}

fn main() -> std::process::ExitCode {
    let args = env::args_os().skip(1).collect::<Vec<_>>();
    let result = match args.as_slice() {
        [flag, input] if flag == "--inspect" => fs::read_to_string(input)
            .map_err(|error| error.to_string())
            .and_then(|wgsl| interface::inspect(&wgsl))
            .map(|output| println!("{output}")),
        [flag, input] if flag == "--validate" => fs::read_to_string(input)
            .map_err(|error| error.to_string())
            .and_then(|wgsl| validate_wgsl(&wgsl)),
        [input, output] => convert(Path::new(input), Path::new(output)),
        _ => Err(
            "Usage: forge-wgsl <input.spv> <output.wgsl> or forge-wgsl --validate|--inspect <input.wgsl>"
                .into(),
        ),
    };
    if let Err(error) = result {
        eprintln!("{error}");
        return std::process::ExitCode::FAILURE;
    }
    std::process::ExitCode::SUCCESS
}
