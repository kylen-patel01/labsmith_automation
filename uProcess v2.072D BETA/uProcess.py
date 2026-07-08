import time
import sys
import win32pipe, win32file, pywintypes
def OpenCommandPipe():
	handle = win32file.CreateFile(r'\\.\pipe\uProcess_Commands',win32file.GENERIC_READ|win32file.GENERIC_WRITE,0,None,win32file.OPEN_EXISTING,0,None)
	win32pipe.SetNamedPipeHandleState(handle, win32pipe.PIPE_READMODE_BYTE, None, None)
	return handle

def CloseCommandPipe(pipe):
        win32file.CloseHandle(pipe)

def SendReceive(handle, cmd):
	win32file.WriteFile(handle, cmd.encode('utf-16le'))
	rerr, raw = win32file.ReadFile(handle, 1024)
	resp = raw.decode('utf-16le');
	return resp
def SetScript(handle, script):
	cmd = "<setscript>"+script;
	ret = SendReceive(handle, cmd)
	time.sleep(0.5)
	return ret
def Run(handle):
	return SendReceive(handle, "<run>")
def Stop(handle):
	return SendReceive(handle, "<stop>")
def Pause(handle):
	return SendReceive(handle, "<pause>")
def StopAll(handle):
	return SendReceive(handle, "<stopall>")
def IsRunning(handle):
	return SendReceive(handle, "<isrunning>")=="yes\r\n"
def IsPaused(handle):
	return SendReceive(handle, "<ispaused>")=="yes\r\n"

	