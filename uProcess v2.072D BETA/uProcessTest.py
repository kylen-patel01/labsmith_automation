import uProcess# Make sure uProcess V2.072 or greater is open before running.
pipe = uProcess.OpenCommandPipe()
#right now the longest script you can send is ~262000 characters
uProcess.SetScript(pipe, ";*ep=uEP01\r\na=1.23\r\nb=sin(a^2)\r\niterations = 100\r\nwhile(iterations >0)\r\n{\r\n\titerations = iterations -1\r\n\tWait(1s)\r\n\tBeep(1)\r\n}\r\n")
uProcess.Run(pipe)
uProcess.IsRunning(pipe)
uProcess.Pause(pipe)
uProcess.IsPaused(pipe)
uProcess.Pause(pipe)
uProcess.IsPaused(pipe)
uProcess.Stop(pipe)
uProcess.IsRunning(pipe)
uProcess.CloseCommandPipe(pipe)