import os
from nilearn._utils.compat import _basestring
from ..externals.nipype.caching import Memory
from ..externals.nipype.interfaces import afni
from ..externals.nipype.utils.filemanip import fname_presuffix
from .struct import anats_to_template
from .utils import _get_output_type
from .base import (BaseSession, extract_brain, _rigid_body_register,
                   _warp, _transform_to_template)


class CESTSession(BaseSession):
    """
    Encapsulation for CEST data, relative to preprocessing.

    Parameters
    ----------
    cest : str
        Path to the CEST image

    anat : str
        Path to anatomical image

    brain_volume : int, optional
        Volume of the brain used for brain extraction.
        Typically 400 for mouse and 1650 for rat.

    output_dir : str, optional
        Path to the output directory. If not specified, current directory is
        used. Final and intermediate images are stored in the subdirectory
        `animal_id` of the given `output_dir`.
    """

    def __init__(self, cest=None, anat=None, brain_volume=None,
                 output_dir=None):
        self.cest = cest
        self.anat = anat
        self.brain_volume = brain_volume
        self.output_dir = output_dir

    def _check_inputs(self):
        if not os.path.isfile(self.cest):
            raise IOError('cest must be an existing image file,'
                          'you gave {0}'.format(self.cest))

        if not os.path.isfile(self.anat):
            raise IOError('anat must be an existing image file,'
                          'you gave {0}'.format(self.anat))

    def coregister(self, use_rats_tool=True,
                   prior_rigid_body_registration=False,
                   caching=False, voxel_size_x=.1, voxel_size_y=.1,
                   verbose=True, **environ_kwargs):
        """
        Coregistration of the animal's CEST and anatomical images.
        The anatomical volume is aligned to the CEST, first with a
        rigid body registration and then a nonlinear warp.

        Parameters
        ----------
        use_rats_tool : bool, optional
            If True, brain mask is computed using RATS Mathematical Morphology.
            Otherwise, a histogram-based brain segmentation is used.

        prior_rigid_body_registration : bool, optional
            If True, a rigid-body registration of the anat to the CEST is
            performed prior to the warp. Useful if the images headers have
            missing/wrong information.

        voxel_size_x : float, optional
            Resampling resolution for the x-axis, in mm.

        voxel_size_y : float, optional
            Resampling resolution for the y-axis, in mm.

        caching : bool, optional
            Wether or not to use caching.

        verbose : bool, optional
            If True, all steps are verbose. Note that caching implies some
            verbosity in any case.

        environ_kwargs : extra arguments keywords
            Extra arguments keywords, passed to interfaces environ variable.

        Returns
        -------
        The following attributes are added
            - `coreg_anat_` : str
                              Path to paths to the coregistered CEST image.
            - `coreg_transform_` : str
                                   Path to the transform from anat to CEST.
        Notes
        -----
        If `use_rats_tool` is turned on, RATS tool is used for brain extraction
        and has to be cited. For more information, see
        `RATS <http://www.iibi.uiowa.edu/content/rats-overview/>`_
        """
        cest_filename = self.cest
        anat_filename = self.anat

        environ = {'AFNI_DECONFLICT': 'OVERWRITE'}
        for (key, value) in environ_kwargs.items():
            environ[key] = value

        if verbose:
            terminal_output = 'allatonce'
        else:
            terminal_output = 'none'

        if caching:
            memory = Memory(self.output_dir)
            copy = memory.cache(afni.Copy)
            unifize = memory.cache(afni.Unifize)
            catmatvec = memory.cache(afni.CatMatvec)
            for step in [copy, unifize]:
                step.interface().set_default_terminal_output(terminal_output)
            overwrite = False
        else:
            copy = afni.Copy(terminal_output=terminal_output).run
            unifize = afni.Unifize(terminal_output=terminal_output).run
            catmatvec = afni.CatMatvec().run
            overwrite = True

        self._check_inputs()
        self._set_output_dir()
        out_copy_cest = copy(
            in_file=self.func,
            out_file=fname_presuffix(self.cest, newpath=self.output_dir),
            environ=environ)
        out_copy_anat = copy(
            in_file=self.anat,
            out_file=fname_presuffix(self.anat, newpath=self.output_dir),
            environ=environ)
        cest_filename = out_copy_cest.outputs.out_file
        anat_filename = out_copy_anat.outputs.out_file
        output_files = [cest_filename, anat_filename]
        outputtype = _get_output_type(cest_filename)  # XXX check if anat and func are of different types
        output_files = []

        ###########################################
        # Corret anat and CEST for intensity bias #
        ###########################################
        # Correct the CEST for intensities bias
        out_bias_correct = unifize(in_file=cest_filename,
                                   outputtype=outputtype, environ=environ)
        unbiased_cest_filename = out_bias_correct.outputs.out_file

        # Bias correct the antomical image
        out_unifize = unifize(in_file=anat_filename, outputtype=outputtype,
                              environ=environ)
        unbiased_anat_filename = out_unifize.outputs.out_file

        # Update outputs
        output_files.extend([unbiased_cest_filename,
                             unbiased_anat_filename])

        ########################################
        # Rigid-body registration anat -> cest #
        ########################################
        if prior_rigid_body_registration:
            allineated_anat_filename, rigid_transform_file = \
                _rigid_body_register(unbiased_anat_filename,
                                     unbiased_cest_filename,
                                     self.output_dir, self.brain_volume,
                                     use_rats_tool=use_rats_tool,
                                     caching=caching,
                                     terminal_output=terminal_output,
                                     environ=environ)
            output_files.extend([rigid_transform_file,
                                 allineated_anat_filename])
        else:
            allineated_anat_filename = unbiased_anat_filename

        #######################################
        # Nonlinear registration anat -> CEST #
        #######################################
        registered_anat_oblique_filename, mat_filename, warp_output_files =\
            _warp(allineated_anat_filename, unbiased_cest_filename,
                  self.output_dir, caching=caching,
                  terminal_output=terminal_output, overwrite=overwrite,
                  environ=environ)

        # Concatenate all the anat to CEST tranforms
        output_files.extend(warp_output_files)
        transform_filename = fname_presuffix(registered_anat_oblique_filename,
                                             suffix='_anat_to_cest.aff12.1D',
                                             use_ext=False)
        _ = catmatvec(in_file=[(mat_filename, 'ONELINE')],
                      oneline=True,
                      out_file=transform_filename)

        if not caching:
            for out_file in output_files:
                os.remove(out_file)

        # Update the CEST data
        setattr(self, "coreg_anat_", registered_anat_oblique_filename)
        setattr(self, "coreg_transform_", transform_filename)

    def register_to_template(self, head_template_filename,
                             brain_template_filename=None,
                             dilated_head_mask_filename=None,
                             prior_rigid_body_registration=False,
                             slice_timing=True,
                             cest_voxel_size=None,
                             maxlev=None,
                             caching=False, verbose=True):
        """ Registration of subject's CEST and anatomical images to
        a given template.

        Parameters
        ----------
        head_template_filename : str
            Template to register the CEST to.

        brain_template_filename : str, optional
            Path to a brain template, passed to
            sammba.registration.anats_to_template

        dilated_head_mask_filename : str, optional
            Path to a dilated head mask, passed to
            sammba.registration.anats_to_template

        cest_voxel_size : 3-tuple of floats, optional
            Voxel size of the registered CEST, in mm.

        maxlev : int or None, optional
            Maximal level for the warp when registering anat to template.
            Passed to
            sammba.registration.anats_to_template

        caching : bool, optional
            Wether or not to use caching.

        verbose : bool, optional
            If True, all steps are verbose. Note that caching implies some
            verbosity in any case.

        Returns
        -------
        The following attributes are added/updated
            - `template_` : str
                           Path to the given registration template.
            - `registered_cest_` : str
                                   Path to the CEST registered to template.
            - `registered_anat_` : str
                                   Path to the anat registered to template.

        See also
        --------
        sammba.registration.anats_to_template
        """
        self._check_inputs()
        if not hasattr(self, 'coreg_transform_'):
            raise ValueError('Anatomical image has not been registered '
                             'to CEAT. Please use `coreg` function first')

        # XXX do a function for creating new attributes ?
        setattr(self, "template_", head_template_filename)
        anats_registration = anats_to_template(
            [self.anat],
            head_template_filename,
            self.output_dir,
            self.brain_volume,
            brain_template_filename=brain_template_filename,
            dilated_head_mask_filename=dilated_head_mask_filename,
            maxlev=maxlev,
            caching=caching, verbose=verbose)
        setattr(self, "registered_anat_", anats_registration.registered[0])

        normalized_cest_filename = _transform_to_template(
            self.cest,
            head_template_filename,
            self.output_dir,
            [self.coreg_transform_, anats_registration.pre_transforms[0],
             anats_registration.transforms[0]],
            voxel_size=cest_voxel_size, caching=caching, verbose=verbose)

        setattr(self, "registered_cest_", normalized_cest_filename)