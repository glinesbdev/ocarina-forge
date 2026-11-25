use naga::{Binding, Handle, Module, Type, TypeInner};
use serde_json::{json, Value};

fn io(
    module: &Module,
    name: Option<&str>,
    ty: Handle<Type>,
    binding: Option<&Binding>,
) -> Vec<Value> {
    if let Some(binding) = binding {
        let address = match binding {
            Binding::BuiltIn(builtin) => json!({ "builtin": format!("{builtin:?}") }),
            Binding::Location {
                location,
                interpolation,
                sampling,
                ..
            } => json!({
                "location": location,
                "interpolation": interpolation.map(|value| format!("{value:?}")),
                "sampling": sampling.map(|value| format!("{value:?}")),
            }),
        };
        return vec![
            json!({ "name": name, "type": format!("{:?}", module.types[ty].inner), "binding": address }),
        ];
    }
    if let TypeInner::Struct { members, .. } = &module.types[ty].inner {
        return members
            .iter()
            .flat_map(|member| {
                io(
                    module,
                    member.name.as_deref(),
                    member.ty,
                    member.binding.as_ref(),
                )
            })
            .collect();
    }
    Vec::new()
}

pub fn inspect(source: &str) -> Result<Value, String> {
    let module = super::parse_wgsl(source)?;
    let info = super::validate(&module).map_err(|error| format!("WGSL validation: {error}"))?;
    let mut layouts = naga::proc::Layouter::default();
    layouts
        .update(module.to_ctx())
        .map_err(|error| error.to_string())?;
    let entry_points: Vec<_> = module
        .entry_points
        .iter()
        .enumerate()
        .map(|(index, entry)| {
            let entry_info = info.get_entry_point(index);
            let inputs: Vec<_> = entry
                .function
                .arguments
                .iter()
                .flat_map(|argument| {
                    io(
                        &module,
                        argument.name.as_deref(),
                        argument.ty,
                        argument.binding.as_ref(),
                    )
                })
                .collect();
            let outputs: Vec<_> = entry
                .function
                .result
                .iter()
                .flat_map(|result| io(&module, None, result.ty, result.binding.as_ref()))
                .collect();
            let resources: Vec<_> = module
                .global_variables
                .iter()
                .filter_map(|(handle, global)| {
                    let address = global.binding.as_ref()?;
                    if entry_info[handle].is_empty() {
                        return None;
                    }
                    let bytes = match global.space {
                        naga::AddressSpace::Uniform | naga::AddressSpace::Storage { .. } => {
                            Some(layouts[global.ty].size)
                        }
                        _ => None,
                    };
                    Some(json!({
                        "name": global.name,
                        "group": address.group,
                        "binding": address.binding,
                        "addressSpace": format!("{:?}", global.space),
                        "type": format!("{:?}", module.types[global.ty].inner),
                        "minimumBufferBytes": bytes,
                    }))
                })
                .collect();
            json!({
                "name": entry.name, "stage": format!("{:?}", entry.stage).to_ascii_lowercase(),
                "inputs": inputs, "outputs": outputs, "resources": resources,
            })
        })
        .collect();
    Ok(json!({ "entryPoints": entry_points }))
}

#[cfg(test)]
mod tests {
    #[test]
    fn reflects_only_bindings_used_by_each_stage() {
        let source = r#"
            @group(0) @binding(1) var<uniform> offset: vec4<f32>;
            @group(0) @binding(2) var<uniform> unused: vec4<f32>;
            struct In { @location(3) position: vec4<f32> }
            @vertex fn main(input: In) -> @builtin(position) vec4<f32> {
                return input.position + offset;
            }"#;
        let output = super::inspect(source).unwrap();
        let entry = &output["entryPoints"][0];
        assert_eq!(entry["stage"], "vertex");
        assert_eq!(entry["inputs"][0]["binding"]["location"], 3);
        assert_eq!(entry["resources"].as_array().unwrap().len(), 1);
        assert_eq!(entry["resources"][0]["binding"], 1);
        assert_eq!(entry["resources"][0]["minimumBufferBytes"], 16);
    }
}
