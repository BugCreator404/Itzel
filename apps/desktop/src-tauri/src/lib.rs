use std::sync::Mutex;
use tauri::{AppHandle, Emitter, Manager};
use tauri_plugin_global_shortcut::{Code, GlobalShortcutExt, Modifiers, Shortcut};

/// Handle del proceso Python del backend. Se guarda para matarlo al cerrar la app.
struct BackendProcess(Mutex<Option<std::process::Child>>);

/// Arranca el backend Python en background.
/// Busca `python` / `python3` en PATH y corre `python -m itzel_core.engine`.
fn spawn_backend(app: &AppHandle) {
    let python = if cfg!(target_os = "windows") { "python" } else { "python3" };

    let child = std::process::Command::new(python)
        .args(["-m", "itzel_core.engine"])
        .env("ITZEL_ENV", "production")
        // Silenciar stdout/stderr del proceso hijo en producción
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
            // Notificar al frontend para que muestre el error
            let _ = app.emit("itzel://backend-error", e.to_string());
        }
    }
}

/// Registra los 6 shortcuts globales del spec.
fn register_shortcuts(app: &AppHandle) {
    let shortcuts = [
        // (modificadores, código, nombre del evento)
        (Modifiers::CONTROL,                        Code::Space,     "open-chat"),
        (Modifiers::CONTROL | Modifiers::SHIFT,     Code::KeyV,      "toggle-voice"),
        (Modifiers::CONTROL | Modifiers::SHIFT,     Code::KeyI,      "open-window"),
        (Modifiers::CONTROL | Modifiers::SHIFT,     Code::KeyS,      "screenshot"),
        (Modifiers::CONTROL | Modifiers::SHIFT,     Code::KeyC,      "explain-clipboard"),
        (Modifiers::CONTROL | Modifiers::SHIFT,     Code::Escape,    "stop-agent"),
    ];

    for (mods, code, name) in shortcuts {
        let shortcut = Shortcut::new(Some(mods), code);
        let app_handle = app.clone();
        let event_name = format!("itzel://shortcut/{name}");

        if let Err(e) = app.global_shortcut().on_shortcut(shortcut, move |_app, _sc, _ev| {
            let _ = app_handle.emit(&event_name, ());
        }) {
            eprintln!("[itzel] Shortcut '{name}' no se pudo registrar: {e}");
        }
    }
}

#[tauri::command]
fn get_backend_url() -> String {
    "http://localhost:7432".to_string()
}

#[tauri::command]
fn get_ws_url() -> String {
    "ws://localhost:7432/ws".to_string()
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .manage(BackendProcess(Mutex::new(None)))
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_global_shortcut::Builder::new().build())
        .plugin(tauri_plugin_clipboard_manager::init())
        .invoke_handler(tauri::generate_handler![get_backend_url, get_ws_url])
        .setup(|app| {
            // Abrir DevTools solo en debug
            #[cfg(debug_assertions)]
            app.get_webview_window("main")
                .expect("ventana principal no encontrada")
                .open_devtools();

            // Arrancar backend Python
            spawn_backend(app.handle());

            // Registrar shortcuts globales
            register_shortcuts(app.handle());

            Ok(())
        })
        // Matar el proceso Python al cerrar la app
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
