# Firma de código y updater — Guía para maintainers

Esta guía explica cómo configurar la firma de los binarios de Itzel y la firma
del auto-updater. **Es opcional**: sin estos secrets, el CI (`build.yml` /
`release.yml`) compila los instaladores igual, solo que **sin firmar**. Los
usuarios verán advertencias del SO al instalar binarios sin firma.

> Principio: Itzel es open source. Cualquiera puede compilar desde el código.
> La firma solo evita las advertencias de "desarrollador no identificado".

---

## Resumen de secrets de GitHub

Configúralos en **Settings → Secrets and variables → Actions → New repository secret**.

| Secret | Para qué | Obligatorio |
|--------|----------|-------------|
| `TAURI_SIGNING_PRIVATE_KEY` | Firma del auto-updater | Solo si usas updater |
| `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` | Password de la clave del updater | Con la anterior |
| `APPLE_CERTIFICATE` | Certificado Developer ID (.p12 en base64) | Solo macOS firmado |
| `APPLE_CERTIFICATE_PASSWORD` | Password del .p12 | Con la anterior |
| `APPLE_SIGNING_IDENTITY` | Nombre del Developer ID Application | Solo macOS firmado |
| `APPLE_ID` | Apple ID para notarización | Solo notarización |
| `APPLE_PASSWORD` | App-specific password | Con la anterior |
| `APPLE_TEAM_ID` | Team ID de la cuenta de desarrollador | Con la anterior |
| `WINDOWS_CERTIFICATE` | Certificado EV (.pfx en base64) | Solo Windows firmado |
| `WINDOWS_CERTIFICATE_PASSWORD` | Password del .pfx | Con la anterior |

---

## 1. Firma del auto-updater (Tauri)

El updater verifica que cada actualización esté firmada con tu clave privada.
Sin esto, el auto-update no funciona (pero la app sí compila e instala).

### Generar el par de claves

```bash
pnpm --filter desktop tauri signer generate -w ~/.tauri/itzel.key
```

Esto produce:
- **Clave privada** (`~/.tauri/itzel.key`) → va al secret `TAURI_SIGNING_PRIVATE_KEY`.
- **Clave pública** (impresa en consola) → va en `tauri.conf.json` → `plugins.updater.pubkey`.

### Cargar la clave privada como secret

```bash
# macOS / Linux
base64 -i ~/.tauri/itzel.key | pbcopy        # macOS
base64 -w0 ~/.tauri/itzel.key                # Linux (copiar la salida)
```

Pega el resultado en el secret `TAURI_SIGNING_PRIVATE_KEY`. Si pusiste password
al generar la clave, ponlo en `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`.

---

## 2. macOS — Code signing + notarización

### Requisitos
- Cuenta **Apple Developer** ($99 USD/año).
- Certificado **Developer ID Application** (Keychain → exportar como `.p12`).

### Exportar el certificado a base64

```bash
base64 -i DeveloperID.p12 | pbcopy
```

Configura los secrets:
- `APPLE_CERTIFICATE` → el base64 del `.p12`
- `APPLE_CERTIFICATE_PASSWORD` → password del `.p12`
- `APPLE_SIGNING_IDENTITY` → p. ej. `Developer ID Application: Edwin Hernández (TEAMID)`

### Notarización

Crea un **app-specific password** en https://appleid.apple.com y configura:
- `APPLE_ID` → tu Apple ID (email)
- `APPLE_PASSWORD` → el app-specific password
- `APPLE_TEAM_ID` → tu Team ID (10 caracteres)

Tauri ejecuta `codesign` y `notarytool` automáticamente cuando estos secrets
están presentes.

---

## 3. Windows — Authenticode (EV recomendado)

### Requisitos
- Certificado **Code Signing** (idealmente **EV** para reputación SmartScreen
  inmediata; ~$300 USD/año).

### Opción A — Certificado en secret (.pfx)

```bash
base64 -w0 certificate.pfx        # copiar la salida
```

- `WINDOWS_CERTIFICATE` → base64 del `.pfx`
- `WINDOWS_CERTIFICATE_PASSWORD` → password del `.pfx`

### Opción B — Thumbprint (certificado en el runner / HSM)

Configura en `tauri.conf.json`:

```json
"bundle": {
  "windows": {
    "certificateThumbprint": "TU_THUMBPRINT",
    "digestAlgorithm": "sha256",
    "timestampUrl": "http://timestamp.digicert.com"
  }
}
```

> Los certificados EV suelen requerir un HSM/token físico, por lo que la firma
> EV completa puede necesitar un runner self-hosted. Para CI hosted, un
> certificado OV (.pfx) vía Opción A es lo más práctico.

---

## 4. Verificar que la firma funcionó

- **macOS**: `codesign --verify --deep --strict Itzel.app` y
  `spctl -a -vvv Itzel.app` (debe decir `accepted`).
- **Windows**: clic derecho en el `.exe` → Propiedades → Firmas digitales.
- **Updater**: el release debe incluir `latest.json` y los `.sig` de cada
  instalador.

---

## 5. Sin firma (estado por defecto)

Si no configuras ningún secret:
- El CI compila y publica binarios **sin firmar**.
- macOS: el usuario debe permitir la app en *Ajustes → Privacidad y seguridad*.
- Windows: SmartScreen mostrará "Windows protegió tu PC" → *Más información → Ejecutar de todas formas*.
- El auto-updater queda inactivo (no hay firma que verificar).

Esto es aceptable para builds de desarrollo y para usuarios que compilan desde
el código fuente.
