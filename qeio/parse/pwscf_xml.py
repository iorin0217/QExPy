import numpy as np
from xmlschema import XMLSchema
from xmlschema.etree import ElementTree

from qe_tools.constants import hartree_to_ev, bohr_to_ang


def cell_volume(a1, a2, a3):
    r"""Returns the volume of the primitive cell: :math:`|\vec a_1\cdot(\vec a_2\cross \vec a_3)|`"""
    a_mid_0 = a2[1] * a3[2] - a2[2] * a3[1]
    a_mid_1 = a2[2] * a3[0] - a2[0] * a3[2]
    a_mid_2 = a2[0] * a3[1] - a2[1] * a3[0]

    return abs(float(a1[0] * a_mid_0 + a1[1] * a_mid_1 + a1[2] * a_mid_2))


def parse_xml(xml_file, dir_with_bands=None, include_deprecated_v2_keys=False):
    xml_parsed = ElementTree.parse(xml_file)
    parsed_data= parse_xml(xml_parsed, include_deprecated_v2_keys)
    return parsed_data

def get_default_schema_filepath():
    """Return the absolute filepath to the default XML schema file.
    :param xml: the pre-parsed XML object
    :return: the XSD absolute filepath
    """
    schema_directory = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'schemas')
    schema_filename = DEFAULT_SCHEMA_FILENAME
    schema_filepath = os.path.join(schema_directory, schema_filename)

    return schema_filepath

def parse_xml(xml, include_deprecated_v2_keys=False):
    """Parse the content of XML output file written by `pw.x` with the new schema-based XML format.
    :param xml: parsed XML
    :param include_deprecated_v2_keys: boolean, if True, includes deprecated keys from old parser v2
    :returns: tuple of two dictionaries, with the parsed data and log messages, respectively
    """
    e_bohr2_to_coulomb_m2 = 57.214766  # e/a0^2 to C/m^2 (electric polarization) from Wolfram Alpha

    schema_filepath_default = get_default_schema_filepath()
    xsd = XMLSchema(schema_filepath_default)

    # Validate XML document against the schema
    # Returned dictionary has a structure where, if tag ['key'] is "simple", xml_dictionary['key'] returns its content.
    # Otherwise, the following keys are available:
    #
    #  xml_dictionary['key']['$'] returns its content
    #  xml_dictionary['key']['@attr'] returns its attribute 'attr'
    #  xml_dictionary['key']['nested_key'] goes one level deeper.

    xml_dictionary, errors = xsd.to_dict(xml, validation='lax')
    if errors:
        pass

    inputs = xml_dictionary['input']
    outputs = xml_dictionary['output']

    lattice_vectors = [
        [x * bohr_to_ang for x in outputs['atomic_structure']['cell']['a1']],
        [x * bohr_to_ang for x in outputs['atomic_structure']['cell']['a2']],
        [x * bohr_to_ang for x in outputs['atomic_structure']['cell']['a3']],
    ]

    has_electric_field = inputs.get('electric_field', {}).get('electric_potential', None) == 'sawtooth_potential'
    has_dipole_correction = inputs.get('electric_field', {}).get('dipole_correction', False)

    occupations = inputs['bands']['occupations']['$']

    starting_magnetization = []
    spin_teta = []
    spin_phi = []

    for specie in outputs['atomic_species']['species']:
        starting_magnetization.append(specie.get('starting_magnetization', 0.0))
        spin_teta.append(specie.get('spin_teta', 0.0))
        spin_phi.append(specie.get('spin_phi', 0.0))

    constraint_mag = 0
    spin_constraints = inputs.get('spin_constraints', {}).get('spin_constraints', None)
    if spin_constraints == 'atomic':
        constraint_mag = 1
    elif spin_constraints == 'atomic direction':
        constraint_mag = 2
    elif spin_constraints == 'total':
        constraint_mag = 3
    elif spin_constraints == 'total direction':
        constraint_mag = 6

    lsda = inputs['spin']['lsda']
    spin_orbit_calculation = inputs['spin']['spinorbit']
    non_colinear_calculation = outputs['magnetization']['noncolin']
    do_magnetization = outputs['magnetization']['do_magnetization']

    # Time reversal symmetry of the system
    if non_colinear_calculation and do_magnetization:
        time_reversal = False
    else:
        time_reversal = True

    # If no specific tags are present, the default is 1
    if non_colinear_calculation or spin_orbit_calculation:
        nspin = 4
    elif lsda:
        nspin = 2
    else:
        nspin = 1

    symmetries = []
    lattice_symmetries = []  # note: will only contain lattice symmetries that are NOT crystal symmetries
    inversion_symmetry = False

    # See also PW/src/setup.f90
    nsym = outputs['symmetries']['nsym']  # crystal symmetries
    nrot = outputs['symmetries']['nrot']  # lattice symmetries

    for symmetry in outputs['symmetries']['symmetry']:

        # There are two types of symmetries, lattice and crystal. The pure inversion (-I) is always a lattice symmetry,
        # so we don't care. But if the pure inversion is also a crystal symmetry, then then the system as a whole
        # has (by definition) inversion symmetry; so we set the global property inversion_symmetry = True.
        symmetry_type = symmetry['info']['$']
        symmetry_name = symmetry['info']['@name']
        if symmetry_type == 'crystal_symmetry' and symmetry_name.lower() == 'inversion':
            inversion_symmetry = True

        sym = {
            'rotation': [
                symmetry['rotation']['$'][0:3],
                symmetry['rotation']['$'][3:6],
                symmetry['rotation']['$'][6:9],
            ],
            'name': symmetry_name,
        }

        try:
            sym['t_rev'] = u'1' if symmetry['info']['@time_reversal'] else u'0'
        except KeyError:
            sym['t_rev'] = u'0'

        try:
            sym['equivalent_atoms'] = symmetry['equivalent_atoms']['$']
        except KeyError:
            pass

        try:
            sym['fractional_translation'] = symmetry['fractional_translation']
        except KeyError:
            pass

        if symmetry_type == 'crystal_symmetry':
            symmetries.append(sym)
        elif symmetry_type == 'lattice_symmetry':
            lattice_symmetries.append(sym)
        else:
            pass

    xml_data = {
        #'pp_check_flag': True, # Currently not printed in the new format.
        # Signals whether the XML file is complete
        # and can be used for post-processing. Everything should be in the XML now, but in
        # any case, the new XML schema should mostly protect from incomplete files.
        'lkpoint_dir': False, # Currently not printed in the new format.
            # Signals whether kpt-data are written in sub-directories.
            # Was generally true in the old format, but now all the eigenvalues are
            # in the XML file, under output / band_structure, so this is False.
        'charge_density': u'./charge-density.dat', # A file name. Not printed in the new format.
            # The filename and path are considered fixed: <outdir>/<prefix>.save/charge-density.dat
            # TODO: change to .hdf5 if output format is HDF5 (issue #222)
        'rho_cutoff_units': 'eV',
        'wfc_cutoff_units': 'eV',
        'fermi_energy_units': 'eV',
        'k_points_units': '1 / angstrom',
        'symmetries_units': 'crystal',
        'constraint_mag': constraint_mag,
        'spin_phi': spin_phi,
        'spin_teta': spin_teta,
        'starting_magnetization': starting_magnetization,
        'has_electric_field': has_electric_field,
        'has_dipole_correction': has_dipole_correction,
        'lda_plus_u_calculation': 'dftU' in outputs,
        'format_name': xml_dictionary['general_info']['xml_format']['@NAME'],
        'format_version': xml_dictionary['general_info']['xml_format']['@VERSION'],
        # TODO: check that format version: a) matches the XSD schema version; b) is updated as well
        #       See line 43 in Modules/qexsd.f90
        'creator_name': xml_dictionary['general_info']['creator']['@NAME'].lower(),
        'creator_version': xml_dictionary['general_info']['creator']['@VERSION'],
        'non_colinear_calculation': non_colinear_calculation,
        'do_magnetization': do_magnetization,
        'time_reversal_flag': time_reversal,
        'symmetries': symmetries,
        'lattice_symmetries': lattice_symmetries,
        'do_not_use_time_reversal': inputs['symmetry_flags']['noinv'],
        'spin_orbit_domag': outputs['magnetization']['do_magnetization'],
        'fft_grid': [value for _, value in sorted(outputs['basis_set']['fft_grid'].items())],
        'lsda': lsda,
        'number_of_spin_components': nspin,
        'no_time_rev_operations': inputs['symmetry_flags']['no_t_rev'],
        'inversion_symmetry': inversion_symmetry,  # the old tag was INVERSION_SYMMETRY and was set to (from the code): "invsym    if true the system has inversion symmetry"
        'number_of_bravais_symmetries': nrot,  # lattice symmetries
        'number_of_symmetries': nsym,          # crystal symmetries
        'wfc_cutoff': inputs['basis']['ecutwfc'] * hartree_to_ev,
        'rho_cutoff': outputs['basis_set']['ecutrho'] * hartree_to_ev,  # not always printed in input->basis
        'smooth_fft_grid': [value for _, value in sorted(outputs['basis_set']['fft_smooth'].items())],
        'dft_exchange_correlation': inputs['dft']['functional'],  # TODO: also parse optional elements of 'dft' tag
            # WARNING: this is different between old XML and new XML
        'spin_orbit_calculation': spin_orbit_calculation,
        'q_real_space': outputs['algorithmic_info']['real_space_q'],
    }

    # alat is technically an optional attribute according to the schema,
    # but I don't know what to do if it's missing. atomic_structure is mandatory.
    output_alat_bohr = outputs['atomic_structure']['@alat']
    output_alat_angstrom = output_alat_bohr * bohr_to_ang

    # Band structure
    if 'band_structure' in outputs:
        band_structure = outputs['band_structure']
        num_k_points = band_structure['nks']
        num_electrons = band_structure['nelec']
        num_atomic_wfc = band_structure['num_of_atomic_wfc']
        num_bands = band_structure.get('nbnd', None)
        num_bands_up = band_structure.get('nbnd_up', None)
        num_bands_down = band_structure.get('nbnd_dw', None)

        # If both channels are `None` we are dealing with a non spin-polarized or non-collinear calculation
        elif num_bands_up is None and num_bands_down is None:
            spins = False

        # Here it is a spin-polarized calculation, where for pw.x the number of bands in each channel should be identical.
        else:
            spins = True
            if num_bands is None:
                num_bands = num_bands_up + num_bands_down   # backwards compatibility;

        k_points = []
        k_points_weights = []
        ks_states = band_structure['ks_energies']
        for ks_state in ks_states:
            k_points.append([kp * 2 * np.pi / output_alat_angstrom for kp in ks_state['k_point']['$']])
            k_points_weights.append(ks_state['k_point']['@weight'])

        if not spins:
            band_eigenvalues = [[]]
            band_occupations = [[]]
            for ks_state in ks_states:
                band_eigenvalues[0].append(ks_state['eigenvalues']['$'])
                band_occupations[0].append(ks_state['occupations']['$'])
        else:
            band_eigenvalues = [[], []]
            band_occupations = [[], []]
            for ks_state in ks_states:
                band_eigenvalues[0].append(ks_state['eigenvalues']['$'][0:num_bands_up])
                band_eigenvalues[1].append(ks_state['eigenvalues']['$'][num_bands_up:num_bands])
                band_occupations[0].append(ks_state['occupations']['$'][0:num_bands_up])
                band_occupations[1].append(ks_state['occupations']['$'][num_bands_up:num_bands])

        band_eigenvalues = np.array(band_eigenvalues) * hartree_to_ev
        band_occupations = np.array(band_occupations)

        if not spins:
            xml_data['number_of_bands'] = num_bands
        else:
            # For collinear spin-polarized calculations `spins=True` and `num_bands` is sum of both channels. To get the
            # actual number of bands, we divide by two using integer division
            xml_data['number_of_bands'] = num_bands // 2

        for key, value in [('number_of_bands_up', num_bands_up), ('number_of_bands_down', num_bands_down)]:
            if value is not None:
                xml_data[key] = value

        if 'fermi_energy' in band_structure:
            xml_data['fermi_energy'] = band_structure['fermi_energy'] * hartree_to_ev

        bands_dict = {
            'occupations': band_occupations,
            'bands': band_eigenvalues,
            'bands_units': 'eV',
        }

        xml_data['number_of_atomic_wfc'] = num_atomic_wfc
        xml_data['number_of_k_points'] = num_k_points
        xml_data['number_of_electrons'] = num_electrons
        xml_data['k_points'] = k_points
        xml_data['k_points_weights'] = k_points_weights
        xml_data['bands'] = bands_dict

    try:
        monkhorst_pack = inputs['k_points_IBZ']['monkhorst_pack']
    except KeyError:
        pass  # not using Monkhorst pack
    else:
        xml_data['monkhorst_pack_grid'] = [monkhorst_pack[attr] for attr in ['@nk1', '@nk2', '@nk3']]
        xml_data['monkhorst_pack_offset'] = [monkhorst_pack[attr] for attr in ['@k1', '@k2', '@k3']]

    if occupations is not None:
        xml_data['occupations'] = occupations

    if 'boundary_conditions' in outputs and 'assume_isolated' in outputs['boundary_conditions']:
        xml_data['assume_isolated'] = outputs['boundary_conditions']['assume_isolated']

    # This is not printed by QE 6.3, but will be re-added before the next version
    if 'real_space_beta' in outputs['algorithmic_info']:
        xml_data['beta_real_space'] = outputs['algorithmic_info']['real_space_beta']

    conv_info = {}
    conv_info_scf = {}
    conv_info_opt = {}
    # NOTE: n_scf_steps refers to the number of SCF steps in the *last* loop only.
    # To get the total number of SCF steps in the run you should sum up the individual steps.
    # TODO: should we parse 'steps' too? Are they already added in the output trajectory?
    for key in ['convergence_achieved', 'n_scf_steps', 'scf_error']:
        try:
            conv_info_scf[key] = outputs['convergence_info']['scf_conv'][key]
        except KeyError:
            pass
    for key in ['convergence_achieved', 'n_opt_steps', 'grad_norm']:
        try:
            conv_info_opt[key] = outputs['convergence_info']['opt_conv'][key]
        except KeyError:
            pass
    if conv_info_scf:
        conv_info['scf_conv'] = conv_info_scf
    if conv_info_opt:
        conv_info['opt_conv'] = conv_info_opt
    if conv_info:
        xml_data['convergence_info'] = conv_info

    if 'status' in xml_dictionary:
        xml_data['exit_status'] = xml_dictionary['status']
        # 0 = convergence reached;
        # -1 = SCF convergence failed;
        # 3 = ionic convergence failed
        # These might be changed in the future. Also see PW/src/run_pwscf.f90


    try:
        berry_phase = outputs['electric_field']['BerryPhase']
    except KeyError:
        pass
    else:
        # This is what I would like to do, but it's not retro-compatible
        # xml_data['berry_phase'] = {}
        # xml_data['berry_phase']['total_phase']         = berry_phase['totalPhase']['$']
        # xml_data['berry_phase']['total_phase_modulus'] = berry_phase['totalPhase']['@modulus']
        # xml_data['berry_phase']['total_ionic_phase']      = berry_phase['totalPhase']['@ionic']
        # xml_data['berry_phase']['total_electronic_phase'] = berry_phase['totalPhase']['@electronic']
        # xml_data['berry_phase']['total_polarization']           = berry_phase['totalPolarization']['polarization']['$']
        # xml_data['berry_phase']['total_polarization_modulus']   = berry_phase['totalPolarization']['modulus']
        # xml_data['berry_phase']['total_polarization_units']     = berry_phase['totalPolarization']['polarization']['@Units']
        # xml_data['berry_phase']['total_polarization_direction'] = berry_phase['totalPolarization']['direction']
        # parser_assert_equal(xml_data['berry_phase']['total_phase_modulus'].lower(), '(mod 2)',
        #                    "Unexpected modulus for total phase")
        # parser_assert_equal(xml_data['berry_phase']['total_polarization_units'].lower(), 'e/bohr^2',
        #                    "Unsupported units for total polarization")
        # Retro-compatible keys:
        polarization = berry_phase['totalPolarization']['polarization']['$']
        polarization_units = berry_phase['totalPolarization']['polarization']['@Units']
        polarization_modulus = berry_phase['totalPolarization']['modulus']
        parser_assert(polarization_units in ['e/bohr^2', 'C/m^2'],
                      "Unsupported units '{}' of total polarization".format(polarization_units))
        if polarization_units == 'e/bohr^2':
            polarization *= e_bohr2_to_coulomb_m2
            polarization_modulus *= e_bohr2_to_coulomb_m2

        xml_data['total_phase'] = berry_phase['totalPhase']['$']
        xml_data['total_phase_units'] = '2pi'
        xml_data['ionic_phase'] = berry_phase['totalPhase']['@ionic']
        xml_data['ionic_phase_units'] = '2pi'
        xml_data['electronic_phase'] = berry_phase['totalPhase']['@electronic']
        xml_data['electronic_phase_units'] = '2pi'
        xml_data['polarization'] = polarization
        xml_data['polarization_module'] = polarization_modulus  # should be called "modulus"
        xml_data['polarization_units'] = 'C / m^2'
        xml_data['polarization_direction'] = berry_phase['totalPolarization']['direction']
        # TODO: add conversion for (e/Omega).bohr (requires to know Omega, the volume of the cell)
        # TODO (maybe): Not parsed:
        # - individual ionic phases
        # - individual electronic phases and weights

    # TODO: We should put the `non_periodic_cell_correction` string in (?)
    atoms = [[atom['@name'], [coord * bohr_to_ang for coord in atom['$']]] for atom in outputs['atomic_structure']['atomic_positions']['atom']]
    species = outputs['atomic_species']['species']
    structure_data = {
        'atomic_positions_units': 'Angstrom',
        'direct_lattice_vectors_units': 'Angstrom',
        # ??? 'atoms_if_pos_list': [[1, 1, 1], [1, 1, 1]],
        'number_of_atoms': outputs['atomic_structure']['@nat'],
        'lattice_parameter': output_alat_angstrom,
        'reciprocal_lattice_vectors': [
            outputs['basis_set']['reciprocal_lattice']['b1'],
            outputs['basis_set']['reciprocal_lattice']['b2'],
            outputs['basis_set']['reciprocal_lattice']['b3']
        ],
        'atoms': atoms,
        'cell': {
            'lattice_vectors': lattice_vectors,
            'volume': cell_volume(*lattice_vectors),
            'atoms': atoms,
        },
        'lattice_parameter_xml': output_alat_bohr,
        'number_of_species': outputs['atomic_species']['@ntyp'],
        'species': {
            'index': [i + 1 for i, specie in enumerate(species)],
            'pseudo': [specie['pseudo_file'] for specie in species],
            'mass': [specie['mass'] for specie in species],
            'type': [specie['@name'] for specie in species]
        },
    }

    xml_data['structure'] = structure_data

    return xml_data