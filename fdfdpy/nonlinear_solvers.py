import numpy as np
import scipy.sparse as sp

import copy

from fdfdpy.linalg import solver_direct
from fdfdpy.constants import *

# Note: for both solvers, the simulation object must have been initialized with the linear permittivity eps_r

def born_solve(simulation, b, nl_region, nonlinear_fn, Estart=None, conv_threshold=1e-8, max_num_iter=10):
	# solves for the nonlinear fields using direct substitution / Born approximation / Picard / whatever you want to call it

	# Defne the starting field for the simulation
	if Estart is None:
		(Hx,Hy,Ez) = simulation.solve_fields(b)
	else: 
		Ez = Estart

	eps_r = simulation.eps_r

	# Stores convergence parameters
	conv_array = np.zeros((max_num_iter, 1))

	# Solve iteratively
	for istep in range(max_num_iter):

		Eprev = Ez

		# set new permittivity
		eps_nl = eps_r + nonlinear_fn(Eprev)*nl_region

		# get new fields
		simulation.reset_eps(eps_nl)
		(Hx, Hy, Ez) = simulation.solve_fields(b)

		# get convergence and break
		convergence = np.linalg.norm(Ez - Eprev)/np.linalg.norm(Ez)
		conv_array[istep] = convergence

		# if below threshold, break and return
		if convergence < conv_threshold:
			break

	if convergence > conv_threshold:
		print("the simulation did not converge, reached {}".format(convergence))

	return (Hx, Hy, Ez, conv_array)


def newton_solve(simulation, b, nl_region, nonlinear_fn, nonlinear_de, 
				Estart=None, conv_threshold=1e-8, max_num_iter=10,
				solver=DEFAULT_SOLVER, matrix_format=DEFAULT_MATRIX_FORMAT):
	# solves for the nonlinear fields using Newton's method
	# Can we break this up into a few functions? -T

	# Defne the starting field for the simulation
	if Estart is None:
		(Hx,Hy,Ez) = simulation.solve_fields(b)
	else: 
		Ez = Estart

	eps_r = simulation.eps_r
	Ez = Ez.reshape(-1,)
	nl_region = nl_region.reshape(-1,)

	# Stores convergence parameters
	conv_array = np.zeros((max_num_iter, 1))

	# num. columns and rows of A
	Nbig = simulation.Nx*simulation.Ny

	# Physical constants
	omega = simulation.omega
	EPSILON_0_ = EPSILON_0*simulation.L0
	MU_0_ = MU_0*simulation.L0

	# Solve iteratively
	for istep in range(max_num_iter):

		Eprev = Ez

		# set new permittivity
		eps_nl = eps_r + (nonlinear_fn(Eprev)*nl_region).reshape(simulation.Nx, simulation.Ny)

		# reset simulation for matrix A (note: you don't need to solve for the fields!) 
		simulation.reset_eps(eps_nl)

		# perform newtons method to get new fields
		Anl = simulation.A 
		fx = (Anl.dot(Eprev) - b.reshape(-1,)*1j*omega).reshape(Nbig, 1)
		dAde = (nonlinear_de(Eprev)*nl_region)*omega**2*EPSILON_0_ 
		Jac11 = Anl + sp.spdiags(dAde*(Eprev), 0, Nbig, Nbig, format=matrix_format)
		Jac12 = sp.spdiags(np.conj(dAde)*(Eprev), 0, Nbig, Nbig, format=matrix_format)

		# Note: I'm phrasing Newton's method as a linear problem to avoid inverting the Jacobian
		# Namely, J*(x_n - x_{n-1}) = -f(x_{n-1}), where J = df/dx(x_{n-1})
		fx_full = np.vstack((fx, np.conj(fx)))
		Jac_full = sp.vstack((sp.hstack((Jac11, Jac12)), np.conj(sp.hstack((Jac12, Jac11)))))
		Ediff = solver_direct(Jac_full, fx_full, solver=solver)
		Ez = Eprev - Ediff[range(Nbig)]

		# get convergence and break
		convergence = np.linalg.norm(Ez - Eprev)/np.linalg.norm(Ez)
		conv_array[istep] = convergence

		# if below threshold, break and return
		if convergence < conv_threshold:
			break

	# Solve the fdfd problem with the final eps_nl
	eps_nl = eps_r + (nonlinear_fn(Ez)*nl_region).reshape(simulation.Nx, simulation.Ny)
	simulation.reset_eps(eps_nl)
	(Hx, Hy, Ez) = simulation.solve_fields(b)

	if convergence > conv_threshold:
		print("the simulation did not converge, reached {}".format(convergence))
		
	return (Hx, Hy, Ez, conv_array)
