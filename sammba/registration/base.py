import os
from ..externals.nipype.caching import Memory
from ..externals.nipype.interfaces import afni
from ..interfaces import segmentation
from .utils import _get_afni_outputtype


def compute_brain_mask(head_file, brain_volume, unifize=True, caching=False,
                       terminal_output='allatonce',
                       use_rats_tool=True, **unifize_kwargs):
    """
    Parameters
    ----------
    brain_volume : int
        Volume of the brain used for brain extraction.
        Typically 400 for mouse and 1800 for rat.

    use_rats_tool : bool, optional
        If True, brain mask is computed using RATS Mathematical Morphology.
        Otherwise, a histogram-based brain segmentation is used.

    caching : bool, optional
        Wether or not to use caching.

    unifize_kwargs : dict, optional
        Is passed to sammba.externals.nipype.interfaces.afni.Unifize.

    Returns
    -------
    path to brain extracted image.

    Notes
    -----
    If `use_rats_tool` is turned on, RATS tool is used for brain extraction
    and has to be cited. For more information, see
    `RATS <http://www.iibi.uiowa.edu/content/rats-overview/>`_
    """
    if use_rats_tool:
        if segmentation.Info().version() is None:
            raise ValueError('Can not locate Rats')
        else:
            ComputeMask = segmentation.MathMorphoMask
    else:
        ComputeMask = segmentation.HistogramMask

    environ = {}
    if caching:
        memory = Memory(os.path.dirname(head_file))
        clip_level = memory.cache(afni.ClipLevel)
        compute_mask = memory.cache(ComputeMask)
        unifize = memory.cache(afni.Unifize)
        for step in [compute_mask, unifize]:
            step.interface().set_default_terminal_output(terminal_output)
    else:
        clip_level = afni.ClipLevel().run
        compute_mask = ComputeMask(terminal_output=terminal_output).run
        unifize = afni.Unifize(terminal_output=terminal_output).run
        environ['AFNI_DECONFLICT'] = 'OVERWRITE'

    if unifize:
        if unifize_kwargs is None:
            unifize_kwargs = {}

        out_unifize = unifize(in_file=head_file,
                              outputtype=_get_afni_outputtype(head_file),
                              environ=environ,
                              **unifize_kwargs)
        head_file = out_unifize.outputs.out_file

    out_clip_level = clip_level(in_file=head_file)
    out_compute_mask = compute_mask(
        in_file=head_file,
        volume_threshold=brain_volume,
        intensity_threshold=int(out_clip_level.outputs.clip_val))

    if not caching and unifize:
        os.remove(out_unifize.outputs.out_file)

    return out_compute_mask.outputs.out_file
