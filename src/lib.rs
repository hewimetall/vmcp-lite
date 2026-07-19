use pyo3::prelude::*;

/// Placeholder GraphQL schema engine exposed to Python.
#[pyclass(name = "SchemaEngine")]
#[derive(Default)]
struct SchemaEngine;

#[pymethods]
impl SchemaEngine {
    #[new]
    fn new() -> Self {
        Self
    }

    fn describe(&self) -> &'static str {
        "vmcp-lite GraphQL schema engine placeholder"
    }
}

#[pyfunction]
fn graphql_stub() -> &'static str {
    "vmcp-lite _graphql extension loaded"
}

#[pymodule]
fn _graphql(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<SchemaEngine>()?;
    m.add_function(wrap_pyfunction!(graphql_stub, m)?)?;
    Ok(())
}
