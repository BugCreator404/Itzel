use std::sync::Mutex;
use tauri::{AppHandle, Emitter, Manager};
use tauri_plugin_global_shortcut::{Code, GlobalShortcutExt, Modifiers, Shortcut};

// ─── estado global ────────────────────────────────────────────────────────────

/// Handle del proceso Python del backend. Se guarda para matarlo al cerrar la app.
struct BackendProcess(Mutex<Option<std::process::Child>>);

// ─── persistencia de posición de ventana ─────────────────────────────────────

#[derive(serde::Serialize, serde::Deserialize, Clone, Copy)]
struct WindowPos {
    x: i32,
    y: i32,
}

#[derive(serde::Serialize, serde::Deserialize, Default)]
struct ItzelConfig {
    chat_window: Option<WindowPos>,
}

/// Ruta al archivo de config: ~/.itzel/itzel.config.json
fn config_path(app: &AppHandle) -> std::path::PathBuf {
    app.path()
        .home_dir()
        .expect("[itzel] No se encontró el directorio home")
        .join(".itzel")
        .join("itzel.config.json")
}

fn read_config(app: &AppHandle) -> ItzelConfig {
    let path = config_path(app);
    if !path.exists() {
        return ItzelConfig::default();
    }
    std::fs::read_to_string(&path)
        .ok()
        .and_then(|raw| serde_json::from_str(&raw).ok())
        .unwrap_or_default()
}

fn write_config(app: &AppHandle, config: &ItzelConfig) -> Result<(), String> {
    let path = config_path(app);
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    }
    let json = serde_json::to_string_pretty(config).map_err(|e| e.to_string())?;
    std::fs::write(&path, json).map_err(|e| e.to_string())
}

// ─── helpers de ventana ───────────────────────────────────────────────────────

/// Alterna la visibilidad de la ventana flotante "chat".
fn toggle_chat(app: &AppHandle) {
    let Some(win) = app.get_webview_window("chat") else {
        return;
    };
    if win.is_visible().unwrap_or(false) {
        let _ = win.hide();
    } else {
        let _ = win.show();
        let _ = win.set_focus();
    }
}

// ─── backend Python ───────────────────────────────────────────────────────────

/// Arranca el backend Python en background.
fn spawn_backend(app: &AppHandle) {
    let python = if cfg!(target_os = "windows") {
        "python"
    } else {
        "python3"
    };

    let child = std::process::Command::new(python)
        .args(["-m", "itzel_core.engine"])
        .env("ITZEL_ENV", "production")
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .spawn();

    match child {
        Ok(proc) => {
            if let Some(state) = app.try_state::<BackendProcess>() {
                *state.0.lock().unwrap() = Some(proc);
            }
        }
        Err(e) => {
            eprintln!("[itzel] No se pudo arrancar el backend Python: {e}");
            let _ = app.emit("itzel://backend-error", e.to_string());
        }
    }
}

// ─── shortcuts globales ───────────────────────────────────────────────────────

fn register_shortcuts(app: &AppHandle) {
    // Ctrl+Space → toggle ventana flotante (manejado en Rust + emite evento)
    {
        let h = app.clone();
        let sc = Shortcut::new(Some(Modifiers::CONTROL), Code::Space);
        if let Err(e) = app.global_shortcut().on_shortcut(sc, move |_, _, _| {
            toggle_chat(&h);
            let _ = h.emit("itzel://shortcut/open-chat", ());
        }) {
            eprintln!("[itzel] Shortcut Ctrl+Space: {e}");
        }
    }

    // Ctrl+Shift+I → mostrar y enfocar ventana principal
    {
        let h = app.clone();
        let sc = Shortcut::new(Some(Modifiers::CONTROL | Modifiers::SHIFT), Code::KeyI);
        if let Err(e) = app.global_shortcut().on_shortcut(sc, move |_, _, _| {
            if let Some(win) = h.get_webview_window("main") {
                let _ = win.show();
                let _ = win.set_focus();
            }
            let _ = h.emit("itzel://shortcut/open-window", ());
        }) {
            eprintln!("[itzel] Shortcut Ctrl+Shift+I: {e}");
        }
    }

    // Shortcuts que solo emiten eventos — el frontend decide qué hacer
    let emit_shortcuts = [
        (
            Modifiers::CONTROL | Modifiers::SHIFT,
            Code::KeyV,
            "toggle-voice",
        ),
        (
            Modifiers::CONTROL | Modifiers::SHIFT,
            Code::KeyS,
            "screenshot",
        ),
        (
            Modifiers::CONTROL | Modifiers::SHIFT,
            Code::KeyC,
            "explain-clipboard",
        ),
        (
            Modifiers::CONTROL | Modifiers::SHIFT,
            Code::Escape,
            "stop-agent",
        ),
        (Modifiers::CONTROL, Code::Slash, "command-palette"),
    ];

    for (mods, code, name) in emit_shortcuts {
        let h = app.clone();
        let event = format!("itzel://shortcut/{name}");
        let sc = Shortcut::new(Some(mods), code);
        if let Err(e) = app.global_shortcut().on_shortcut(sc, move |_, _, _| {
            let _ = h.emit(&event, ());
        }) {
            eprintln!("[itzel] Shortcut '{name}': {e}");
        }
    }
}

// ─── aplicar posición guardada al arranque ────────────────────────────────────

fn apply_saved_position(app: &AppHandle) {
    let config = read_config(app);
    let Some(pos) = config.chat_window else {
        return;
    };
    let Some(win) = app.get_webview_window("chat") else {
        return;
    };
    let _ = win.set_position(tauri::PhysicalPosition::new(pos.x, pos.y));
}

// ─── comandos Tauri (invocables desde el frontend) ───────────────────────────

#[tauri::command]
fn get_backend_url() -> String {
    "http://localhost:7432".to_string()
}

#[tauri::command]
fn get_ws_url() -> String {
    "ws://localhost:7432/ws".to_string()
}

/// Alterna la ventana flotante de chat.
#[tauri::command]
fn toggle_chat_window(app: AppHandle) -> Result<(), String> {
    let win = app
        .get_webview_window("chat")
        .ok_or_else(|| "Ventana 'chat' no encontrada".to_string())?;
    if win.is_visible().map_err(|e| e.to_string())? {
        win.hide().map_err(|e| e.to_string())
    } else {
        win.show().map_err(|e| e.to_string())?;
        win.set_focus().map_err(|e| e.to_string())
    }
}

/// Guarda la posición de la ventana flotante en ~/.itzel/itzel.config.json
#[tauri::command]
fn save_chat_position(app: AppHandle, x: i32, y: i32) -> Result<(), String> {
    let mut config = read_config(&app);
    config.chat_window = Some(WindowPos { x, y });
    write_config(&app, &config)
}

/// Lee la posición guardada de la ventana flotante.
/// Devuelve null si no hay posición guardada.
#[tauri::command]
fn load_chat_position(app: AppHandle) -> Option<WindowPos> {
    read_config(&app).chat_window
}

// ─── entrada principal ────────────────────────────────────────────────────────

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let builder = tauri::Builder::default()
        .manage(BackendProcess(Mutex::new(None)))
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_global_shortcut::Builder::new().build())
        .plugin(tauri_plugin_clipboard_manager::init());

    // Auto-updater + process (solo desktop; no existen en Android/iOS).
    #[cfg(desktop)]
    let builder = builder
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_process::init());

    builder
        .invoke_handler(tauri::generate_handler![
            get_backend_url,
            get_ws_url,
            toggle_chat_window,
            save_chat_position,
            load_chat_position,
        ])
        .setup(|app| {
            #[cfg(debug_assertions)]
            app.get_webview_window("main")
                .expect("ventana principal no encontrada")
                .open_devtools();

            spawn_backend(app.handle());
            register_shortcuts(app.handle());
            apply_saved_position(app.handle());

            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::Destroyed = event {
                if let Some(state) = window.try_state::<BackendProcess>() {
                    if let Some(mut child) = state.0.lock().unwrap().take() {
                        let _ = child.kill();
                    }
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("error al arrancar Itzel");
}
