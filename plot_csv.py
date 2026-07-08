import pandas as pd
import matplotlib.pyplot as plt

PATH = r'C:/Users/kylenp/Desktop/LabSmith/Automation/ExperimentData/ReactionChamberChip/exp_020226/ReactionChamberChip_RES4_R1_20260202_133252.csv'

with open(PATH, 'r', encoding='utf-8', errors='ignore') as fh:
	first = fh.readline()
if first.startswith('sep='):
	sep = first.strip().split('=', 1)[1]
	df = pd.read_csv(PATH, sep=sep, skiprows=1)
else:
	df = pd.read_csv(PATH, sep=';')

# normalize column names and plot
df.columns = df.columns.str.strip()
plt.plot(df['Time (s)'], df.filter(regex='SF06|SF06_5', axis=1).iloc[:,0])
plt.xlabel('Time (s)')
plt.show()