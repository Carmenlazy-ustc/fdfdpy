import sys
sys.path.append(".")

from adjoint import adjoint_linear, adjoint_nonlinear

import numpy as np
import copy
import progressbar
import matplotlib.pylab as plt
from scipy.optimize import minimize, fmin_l_bfgs_b
from fdfdpy.constants import *
from autograd import grad
from filter import (eps2rho, rho2eps, get_W, deps_drhob, drhob_drhot,
                    drhot_drho, rho2rhot, drhot_drho, rhot2rhob)


class Optimization():

    def __init__(self, J=None, simulation=None, design_region=None, eps_m=5,
                 R=5, eta=0.5, beta=100):

        self._J = J
        self.simulation = simulation
        self.design_region = design_region

        self.eps_m = eps_m
        self.R = R

        (Nx, Ny) = self.simulation.eps_r.shape
        self.W = get_W(Nx, Ny, self.design_region, R=self.R)
        self.eta = eta
        self.beta = beta

        self.objfn_list = []

        # compute the jacobians of J and store these
        self.dJ = self._autograd_dJ(J)

    @property
    def J(self):
        return self._J

    @J.setter
    def J(self, J):
        self._J = J
        self.dJ = self._autograd_dJ(J)

    def _autograd_dJ(self, J):
        """ Uses autograd to automatically compute Jacobians of J with respect to each argument"""

        # note: eventually want to check whether J has eps_nl argument, then switch between linear and nonlinear depending.
        dJ = {}
        dJ['lin'] = grad(J, 0)
        return dJ

    def compute_J(self, simulation):
        """ Returns the current objective function of a simulation"""

        if simulation.fields['Ez'] is None:
            (_, _, Ez) = simulation.solve_fields()
        else:
            Ez = simulation.fields['Ez']

        return self.J(Ez)

    def compute_dJ(self, simulation, design_region):
        """ Returns the current grad of a simulation"""

        if simulation.fields['Ez'] is None:
            (_, _, Ez) = simulation.solve_fields()
        else:
            Ez = simulation.fields['Ez']

        return self._grad_linear(Ez)

    def _grad_linear(self, Ez, Ez_nl):
        """gives the linear field gradient: partial J/ partial * E_lin dE_lin / deps"""

        b_aj = -self.dJ['lin'](Ez, Ez_nl)
        Ez_aj = adjoint_linear(self.simulation, b_aj)

        EPSILON_0_ = EPSILON_0*self.simulation.L0
        omega = self.simulation.omega
        dAdeps = self.design_region*omega**2*EPSILON_0_

        rho = self.simulation.rho
        rho_t = rho2rhot(rho, self.W)
        rho_b = rhot2rhob(rho_t, eta=self.eta, beta=self.beta)
        eps_mat = (self.eps_m - 1)

        filt_mat = drhot_drho(self.W)
        proj_mat = drhob_drhot(rho_t, eta=self.eta, beta=self.beta)

        Ez_vec = np.reshape(Ez, (-1,))

        dAdeps_vec = np.reshape(dAdeps, (-1,))
        dfdrho = eps_mat*filt_mat.multiply(Ez_vec*proj_mat*dAdeps_vec)
        Ez_aj_vec = np.reshape(Ez_aj, (-1,))
        sensitivity_vec = dfdrho.dot(Ez_aj_vec)        

        return 1*np.real(np.reshape(sensitivity_vec, rho.shape))

    def check_deriv(self, Npts=5, d_rho=1e-3):
        """ Returns a list of analytical and numerical derivatives to check grad accuracy"""

        self.simulation.eps_r = rho2eps(rho=self.simulation.rho, eps_m=self.eps_m, W=self.W,
                                        eta=self.eta, beta=self.beta)
        self.simulation.solve_fields()

        # solve for the linear fields and grad of the linear objective function
        grad_avm = self.compute_dJ(self.simulation, self.design_region)
        J_orig = self.compute_J(self.simulation)

        avm_grads = []
        num_grads = []

        # for a number of points
        for _ in range(Npts):

            # pick a random point within the design region
            x, y = np.where(self.design_region == 1)
            i = np.random.randint(len(x))
            pt = [x[i], y[i]]

            # create a new, perturbed permittivity
            rho_new = copy.deepcopy(self.simulation.rho)
            rho_new[pt[0], pt[1]] += d_rho

            # make a copy of the current simulation
            sim_new = copy.deepcopy(self.simulation)
            eps_new = rho2eps(rho=rho_new, eps_m=self.eps_m, W=self.W,
                              eta=self.eta, beta=self.beta)

            sim_new.rho = rho_new
            sim_new.eps_r = eps_new

            sim_new.solve_fields()

            # solve for the fields with this new permittivity
            J_new = self.compute_J(sim_new)

            # compute the numerical grad
            grad_num = (J_new - J_orig) / d_rho

            # append both grads to lists
            avm_grads.append(grad_avm[pt[0], pt[1]])
            num_grads.append(grad_num)

        return avm_grads, num_grads

    def _make_progressbar(self, N):
        """ Returns a progressbar to use during optimization"""

        bar = progressbar.ProgressBar(widgets=[
            ' ', progressbar.DynamicMessage('ObjectiveFn'),
            ' Iteration: ',
            ' ', progressbar.Counter(), '/%d' % N,
            ' ', progressbar.AdaptiveETA(),
        ], max_value=N)

    def _update_progressbar(self, pbar, iteration, J):

        if self.max_ind_shift is not None:
            objfn_norm = J/np.max(np.square(np.abs(self.simulation.src)))
            pbar.update(iteration, ObjectiveFn=J, ObjectiveFn_Normalized=objfn_norm)
        else:
            pbar.update(iteration, ObjectiveFn=J)

    def run(self, method='LBFGS', Nsteps=100, step_size=0.1,
            beta1=0.9, beta2=0.999, verbose=True):
        """ Runs an optimization."""

        self.Nsteps = Nsteps
        self.verbose = verbose

        # get the material density from the simulation if only the first time being run
        if self.simulation.rho is None:
            eps = copy.deepcopy(self.simulation.eps_r)
            self.simulation.rho = eps2rho(eps)

        allowed = ['LBFGS', 'GD', 'ADAM']

        if method.lower() in ['lbfgs']:
            self._run_LBFGS()

        elif method.lower() == 'gd':
            self._run_GD(step_size=step_size)

        elif method.lower() == 'adam':
            self._run_ADAM(step_size=step_size, beta1=beta1, beta2=beta2)

        else:
            raise ValueError("'method' must be in {}".format(allowed))

    def _run_GD(self, step_size):
        """ Performs simple grad descent optimization"""

        pbar = self._make_progressbar(self.Nsteps)

        for iteration in range(self.Nsteps):

            J = self.compute_J(self.simulation)
            self.objfn_list.append(J)

            self._update_progressbar(pbar, iteration, J)

            grad = self.compute_dJ(self.simulation, self.design_region)

            self._update_rho(grad, step_size)

    def _run_ADAM(self, step_size, beta1, beta2):
        """ Performs simple grad descent optimization"""

        pbar = self._make_progressbar(self.Nsteps)

        for iteration in range(self.Nsteps):

            J = self.compute_J(self.simulation)
            self.objfn_list.append(J)
            # pbar.update(iteration, ObjectiveFn=J)
            self._update_progressbar(pbar, iteration, J)

            grad = self.compute_dJ(self.simulation, self.design_region)

            if iteration == 0:
                mopt = np.zeros(grad.shape)
                vopt = np.zeros(grad.shape)

            (grad_adam, mopt, vopt) = self._step_adam(grad, mopt, vopt, iteration, beta1, beta2,)

            self._update_rho(grad_adam, step_size)

    def _run_LBFGS(self):
        """Performs L-BFGS Optimization of objective function w.r.t. eps_r"""

        pbar = self._make_progressbar(self.Nsteps)

        def _objfn(rho, *argv):
            """ Returns objective function given some permittivity distribution"""

            self._set_design_region(rho)
            J = self.compute_J(self.simulation)

            # return minus J because we technically will minimize
            return -J

        def _grad(rho,  *argv):
            """ Returns full grad given some permittivity distribution"""

            self._set_design_region(rho)

            # compute grad, extract design region, turn into vector, return
            grad = self.compute_dJ(self.simulation, self.design_region)
            grad_vec = self._get_design_region(grad)

            return -grad_vec

        # this simple callback function gets run each iteration
        # keeps track of the current iteration step for the progressbar
        # also resets eps on the simulation
        iter_list = [0]

        def _update_iter_count(x_current):
            J = self.compute_J(self.simulation)
            self._update_progressbar(pbar, iter_list[0], J)
            iter_list[0] += 1
            self.objfn_list.append(J)
            self._set_design_region(x_current)

        N_des = np.sum(self.design_region == 1)              # num points in design region
        rho_bounds = tuple([(0, 1) for _ in range(N_des)])   # bounds on rho {0, 1}

        # start eps off with the one currently within design region
        rho = copy.deepcopy(self.simulation.rho)
        rho0 = self._get_design_region(rho)
        rho0 = np.reshape(rho0, (-1,))

        # minimize
        (rho_final, _, _) = fmin_l_bfgs_b(_objfn, rho0, fprime=_grad, args=(), approx_grad=0,
                            bounds=rho_bounds, m=10, factr=10,
                            pgtol=1e-15, epsilon=1e-08, iprint=-1,
                            maxfun=15000, maxiter=self.Nsteps, disp=self.verbose,
                            callback=_update_iter_count, maxls=20)

        # finally, set the simulation permittivity to that found via optimization
        self._set_design_region(rho_final)

    def _set_design_region(self, x):
        """ Inserts a vector x into design region of simulation.rho """

        rho_vec = np.reshape(copy.deepcopy(self.simulation.rho), (-1,))
        des_vec = np.reshape(self.design_region, (-1,))

        # Only update the rho if it actually differs from the current one
        # If it doesn't, we don't want to erase the stored fields

        if np.linalg.norm(x - rho_vec[des_vec == 1])/np.linalg.norm(x) > 1e-10:
            rho_vec[des_vec == 1] = x
            rho_new = np.reshape(rho_vec, self.simulation.rho.shape)
            self.simulation.rho = rho_new
            eps_new = rho2eps(rho=rho_new, eps_m=self.eps_m, W=self.W,
                              eta=self.eta, beta=self.beta)
            self.simulation.eps_r = eps_new

    def _get_design_region(self, spatial_array):
        """ Returns a vector of the elements of spatial_array that are in design_region"""

        spatial_vec = copy.deepcopy(np.ndarray.flatten(spatial_array))
        des_vec = np.ndarray.flatten(self.design_region)
        x = spatial_vec[des_vec == 1]
        return x

    def _update_rho(self, grad, step_size):
        """ Manually updates the permittivity with the grad info """

        self.simulation.rho = self.simulation.rho + self.design_region * step_size * grad
        self.simulation.rho[self.simulation.rho < 0] = 0
        self.simulation.rho[self.simulation.rho > 1] = 1

        self.simulation.eps_r = rho2eps(self.simulation.rho, self.eps_m, self.W,
                                        eta=self.eta, beta=self.beta)

    def _step_adam(self, gradient, mopt_old, vopt_old, iteration, beta1, beta2, epsilon=1e-8):
        """ Performs one step of adam optimization"""

        mopt = beta1 * mopt_old + (1 - beta1) * gradient
        mopt_t = mopt / (1 - beta1**(iteration + 1))
        vopt = beta2 * vopt_old + (1 - beta2) * (np.square(gradient))
        vopt_t = vopt / (1 - beta2**(iteration + 1))
        grad_adam = mopt_t / (np.sqrt(vopt_t) + epsilon)

        return (grad_adam, mopt, vopt)

    def plt_objs(self, ax=None):
        """ Plots objective function vs. iteration"""

        iter_range = range(1, len(self.objfn_list) + 1)

        if ax is None:
            f, ax = plt.subplots(1)

        ax.plot(iter_range,  self.objfn_list)
        ax.set_xlabel('iteration number')
        ax.set_ylabel('objective function')        
        ax.set_title('optimization results')

        return ax

    def scan_frequency(self, Nf=50, df=1/20):
        """ Scans the objective function vs. frequency """

        # create frequencies (in Hz)
        delta_f = self.simulation.omega*df
        freqs = 1/2/np.pi*np.linspace(self.simulation.omega - delta_f/2,
                                      self.simulation.omega + delta_f/2,  Nf)

        bar = progressbar.ProgressBar(max_value=Nf)

        # loop through frequencies
        objs = []
        for i, f in enumerate(freqs):

            bar.update(i + 1)

            # make a new simulation object
            sim_new = copy.deepcopy(self.simulation)

            # reset the simulation to compute new A (hacky way of doing it)
            sim_new.omega = 2*np.pi*f
            sim_new.eps_r = self.simulation.eps_r

            # compute objective function and append to list
            obj_fn = self.compute_J(sim_new)
            objs.append(obj_fn)

        # compute HM
        objs_array = np.array(objs)
        HM = np.max(objs_array)/2
        above_HM = objs_array > HM

        # does a scan up and down from the midpoint and counts number above HM in this peak
        num_above_HM = 0
        for i in range(int(Nf/2), Nf):
            if not above_HM[i]:
                break
            num_above_HM += 1
        for i in range(int(Nf/2)-1, -1, -1):
            if not above_HM[i]:
                break
            num_above_HM += 1

        # compute FWHM (Hz) using the number above HM and the freq difference
        FWHM = num_above_HM*(freqs[1] - freqs[0])

        return freqs, objs, FWHM
