//go:build !headless

package main

import (
	"log"
	"os"
	"unsafe"

	"golang.org/x/sys/windows"
)

func installTrayDoubleClick(open func()) {
	user := windows.NewLazySystemDLL("user32.dll")
	enum := user.NewProc("EnumWindows")
	getPID := user.NewProc("GetWindowThreadProcessId")
	getClass := user.NewProc("GetClassNameW")
	var window uintptr
	find := windows.NewCallback(func(hwnd, _ uintptr) uintptr {
		var pid uint32
		getPID.Call(hwnd, uintptr(unsafe.Pointer(&pid)))
		if pid == uint32(os.Getpid()) {
			var name [64]uint16
			getClass.Call(hwnd, uintptr(unsafe.Pointer(&name[0])), uintptr(len(name)))
			if windows.UTF16ToString(name[:]) == "SystrayClass" {
				window = hwnd
				return 0
			}
		}
		return 1
	})
	enum.Call(find, 0)
	if window == 0 {
		log.Print("Vvuzz tray window not found")
		return
	}
	call := user.NewProc("CallWindowProcW")
	index := ^uintptr(3) // GWLP_WNDPROC (-4), Windows x64 distribution.
	previous, _, err := user.NewProc("GetWindowLongPtrW").Call(window, index)
	if previous == 0 {
		log.Print("cannot inspect tray window: ", err)
		return
	}
	callback := windows.NewCallback(func(hwnd uintptr, message uint32, wParam, lParam uintptr) uintptr {
		// shortcut: pinned systray v1.2.2 uses WM_USER+1; recheck when upgrading it.
		if message == 0x0401 {
			if lParam == 0x0203 {
				go open()
				return 0
			} // WM_LBUTTONDBLCLK
			if lParam == 0x0202 {
				return 0
			} // Leave the menu on right click.
		}
		result, _, _ := call.Call(previous, hwnd, uintptr(message), wParam, lParam)
		return result
	})
	replaced, _, err := user.NewProc("SetWindowLongPtrW").Call(window, index, callback)
	if replaced == 0 {
		log.Print("cannot configure tray double click: ", err)
	}
}
