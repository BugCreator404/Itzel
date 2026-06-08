use tauri::Manager;

#[tauri::command]
fn greet(name: &str) -> String {
    format!("¡Hola, {}! Soy Itzel 🦎", name)
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_global_shortcut::Builder::new().build())
        .plugin(tauri_plugin_clipboard_manager::init())
        .invoke_handler(tauri::generate_handler![greet])
        .setup(|app| {
            #[cfg(debug_assertions)]
            app.get_webview_window("main")
                .expect("ventana principal no encontrada")
                .open_devtools();
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error al arrancar Itzel");
}
