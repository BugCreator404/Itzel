// Oculta la ventana de consola en Windows en builds release
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    itzel_desktop_lib::run();
}
