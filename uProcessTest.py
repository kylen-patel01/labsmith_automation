import uProcess# Make sure uProcess V2.072 or greater is open before running.
import os
pipe = uProcess.OpenCommandPipe()
#right now the longest script you can send is ~262000 characters
base_dir = "C:/Users/kylenp/Desktop/LabSmith/Automation"
os.makedirs(base_dir, exist_ok = True)
test_usq = os.path.join(base_dir, "test.usq")
header = f"flow = 100\r\n"
with open(test_usq, "r", encoding = "ascii", errors = "replace") as f:
    base = f.read()
script = header + base
t = repr(base)
s = ";Script\r\n flow = 100\r\n pressure = 23 kPa \r\n *4VM02\t = \t4VM\r\n 4VM02: SetValves(1,0,0,0)\r\n WaitDone()\r\n"
r = uProcess.SetScript(pipe, s)
print(repr(base))
uProcess.Run(pipe)
uProcess.IsRunning(pipe)
uProcess.Pause(pipe)
uProcess.IsPaused(pipe)
uProcess.Pause(pipe)
uProcess.IsPaused(pipe)
uProcess.Stop(pipe)
uProcess.IsRunning(pipe)
uProcess.CloseCommandPipe(pipe)
