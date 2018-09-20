import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from fdfdpy import Simulation
from numpy import pi, ones

omega = 2*pi*200e12
dl = 0.01
eps_r = ones((300, 100))
eps_r[:, 40:60] = 12.25
NPML = [15, 15]

simulation1 = Simulation(omega, eps_r, dl, NPML, 'Ez')
simulation1.add_mode(3.5, 'x', [20, 50], 60, scale=1)
simulation1.setup_modes()
simulation1.solve_fields()
flux1 = simulation1.flux_probe('x', [150, 50], 60)
print('Flux = {} W/L0'.format(flux1))

omega = 2*pi*200e12
dl = 0.005
eps_r = ones((600, 200))
eps_r[:,80:120] = 12.25
NPML = [15, 15]
simulation2 = Simulation(omega, eps_r, dl, NPML, 'Ez')
simulation2.add_mode(3.5, 'x', [20, 100], 120, scale=1)
simulation2.setup_modes()
simulation2.solve_fields()
flux2 = simulation2.flux_probe('x', [300, 100], 120)
print('Flux = {} W/L0'.format(flux2))