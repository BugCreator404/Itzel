# Marca la raíz del repo como rootdir de pytest.
# Sin este archivo pytest toma packages/core como rootdir (por su pyproject.toml)
# y no puede descubrir los tests de apps/cli.
