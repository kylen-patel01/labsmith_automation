import numpy as np
import matplotlib.pyplot as plt
A = 2.414E-5
B = 247.8
C = 140.0
temperature = np.linspace(0, 100, 100)  # Example temperature range from 0 to 100 degrees Celsius 
viscosity = A*(10 ** (B / (temperature + C))) *1000
plt.plot(temperature, viscosity)
plt.xlabel("Temperature (C)")
plt.ylabel("Viscosity (mPa s)")
plt.title("Viscosity vs. Temperature (DI Water)")
plt.grid()
plt.show()