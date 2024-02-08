import numpy as np


# Define default export dictionary
export_def = {
    'step_export': True,
    'h5m_export': None,
    'dir': '',
    'h5m_filename': 'dagmc',
    'native_meshing': False,
    'facet_tol': None,
    'len_tol': None,
    'norm_tol': None,
    'anisotropic_ratio': 100,
    'deviation_angle': 5,
    'min_mesh_size': 5.0,
    'max_mesh_size': 20.0,
    'volume_atol': 0.00001,
    'center_atol': 0.00001,
    'bounding_box_atol': 0.00001
}


def NWL_geom(
        plas_eq, wall_s, tor_ext, num_phi = 61, num_theta = 61, source = None, export = export_def, logger = None
    ):
    """Creates DAGMC-compatible neutronics H5M geometry of first wall.

    Arguments:
        plas_eq (str): path to plasma equilibrium NetCDF file.
        wall_s (float): closed flux surface label extrapolation at wall.
        tor_ext (float): toroidal extent to model (deg).
        num_phi (int): number of phi geometric cross-sections to make for each
            build segment (defaults to 61).
        num_theta (int): number of points defining the geometric cross-section
            (defaults to 61).
        source (dict): dictionary of source mesh parameters including
            {
                'num_s': number of closed magnetic flux surfaces defining mesh
                    (int),
                'num_theta': number of poloidal angles defining mesh (int),
                'num_phi': number of toroidal angles defining mesh (int)
            }
        export (dict): dictionary of export parameters including
            {
                'step_export': export component STEP files (bool, defaults to
                    True),
                'h5m_export': export DAGMC-compatible neutronics H5M file using
                    Cubit or Gmsh. Acceptable values are None or a string value
                    of 'Cubit' or 'Gmsh' (str, defaults to None). The string is
                    case-sensitive. Note that if magnets are included, 'Cubit'
                    must be used,
                'dir': directory to which to export output files (str, defaults
                    to empty string). Note that directory must end in '/', if
                    using Linux or MacOS, or '\' if using Windows.
                'h5m_filename': name of DAGMC-compatible neutronics H5M file
                    (str, defaults to 'dagmc'),
                'native_meshing': choose native or legacy faceting for DAGMC
                    export (bool, defaults to False),
                'facet_tol': maximum distance a facet may be from surface of
                    CAD representation for Cubit export (float, defaults to
                    None),
                'len_tol': maximum length of facet edge for Cubit export
                    (float, defaults to None),
                'norm_tol': maximum change in angle between normal vector of
                    adjacent facets (float, defaults to None),
                'anisotropic_ratio': controls edge length ratio of elements
                    (float, defaults to 100.0),
                'deviation_angle': controls deviation angle of facet from
                    surface, i.e. lower deviation angle => more elements in
                    areas with higher curvature (float, defaults to 5.0),
                'min_mesh_size': minimum mesh element size for Gmsh export
                    (float, defaults to 5.0),
                'max_mesh_size': maximum mesh element size for Gmsh export
                    (float, defaults to 20.0),
                'volume_atol': absolute volume tolerance to allow when matching
                    parts in intermediate BREP file with CadQuery parts for
                    Gmsh export(float, defaults to 0.00001),
                'center_atol': absolute center coordinates tolerance to allow
                    when matching parts in intermediate BREP file with CadQuery
                    parts for Gmsh export (float, defaults to 0.00001),
                'bounding_box_atol': absolute bounding box tolerance  to allow
                    when matching parts in intermediate BREP file with CadQuery
                    parts for Gmsh export (float, defaults to 0.00001).
            }
        logger (object): logger object (defaults to None). If no logger is
            supplied, a default logger will be instantiated.

    Returns:
        strengths (list): list of source strengths for each tetrahedron (1/s).
            Returned only if source mesh is generated.
    """
    import parastell as ps
    import source_mesh as sm
    import log
    import read_vmec
    import cubit
    from scipy.interpolate import RegularGridInterpolator
    import os
    import inspect
    
    export_dict = export_def.copy()
    export_dict.update(export)
    
    if export_dict['h5m_export'] == 'Cubit':
        cubit_dir = os.path.dirname(inspect.getfile(cubit))
        cubit_dir = cubit_dir + '/plugins/'
        cubit.init([
            'cubit',
            '-nojournal',
            '-nographics',
            '-information', 'off',
            '-warning', 'off',
            '-commandplugindir',
            cubit_dir
        ])
    
    if logger == None or not logger.hasHandlers():
        logger = log.init()

    logger.info('Building first wall geometry...')
    
    vmec = read_vmec.vmec_data(plas_eq)

    repeat = 0

    # Define arrays of toroidal and poloidal angles of first wall build
    tor_ext = np.deg2rad(tor_ext)
    phi_list = np.linspace(0, tor_ext, num = num_phi)
    theta_list = np.linspace(0, 2*np.pi, num = num_theta)
    
    # Define offset matrix
    offset_mat = np.zeros((num_phi, num_theta))
    
    # Define volume used to cut periods
    cutter = None

    # Build offset interpolator
    interp = RegularGridInterpolator((phi_list, theta_list), offset_mat)

    # Generate stellarator torus with exterior surface at first wall
    try:
        torus, cutter = ps.stellarator_torus(
            vmec, wall_s, tor_ext, repeat, phi_list, theta_list, interp, cutter
        )
    except ValueError as e:
        logger.error(e.args[0])
        raise e

    components = {
        'first_wall': {
            'solid': torus,
            'h5m_tag': 'Vacuum'
        }
    }

    try:
        ps.exports(export, components, None, logger)
    except ValueError as e:
        logger.error(e.args[0])
        raise e

    if source is not None:
        strengths = sm.source_mesh(vmec, source, logger = logger)
        
        file = open('strengths.txt', 'w')
        for tet in strengths:
            file.write(f'{tet}\n')
        
        return strengths


def extract_ss(ss_file):
    """Extracts list of source strengths for each tetrahedron from input file.

    Arguments:
        ss_file (str): source strength input file.

    Returns:
        strengths (list): list of source strengths for each tetrahedron (1/s).
            Returned only if source mesh is generated.
    """
    strengths = []
    
    file_obj = open(ss_file, 'r')
    data = file_obj.readlines()
    for line in data:
        strengths.append(float(line))

    return strengths


def NWL_transport(dagmc_geom, source_mesh, tor_ext, ss_file, num_parts):
    """Performs neutron transport on first wall geometry via OpenMC.

    Arguments:
        dagmc_geom (str): path to DAGMC geometry file.
        source_mesh (str): path to source mesh file.
        tor_ext (float): toroidal extent of model (deg).
        ss_file (str): source strength input file.
        num_parts (int): number of source particles to simulate.
    """
    import openmc
    
    tor_ext = np.deg2rad(tor_ext)
    
    strengths = extract_ss(ss_file)

    # Initialize OpenMC model
    model = openmc.model.Model()

    dag_univ = openmc.DAGMCUniverse(dagmc_geom, auto_geom_ids = False)

    # Define problem boundaries
    vac_surf = openmc.Sphere(
        r = 10000, surface_id = 9999, boundary_type = 'vacuum'
    )
    per_init = openmc.YPlane(
        boundary_type = 'periodic',
        surface_id = 9991
    )
    per_fin = openmc.Plane(
        a = np.sin(tor_ext),
        b = -np.cos(tor_ext),
        c = 0,
        d = 0,
        boundary_type = 'periodic',
        surface_id = 9990
    )

    # Define first period of geometry
    region  = -vac_surf & +per_init & +per_fin
    period = openmc.Cell(cell_id = 9996, region = region, fill = dag_univ)
    geometry = openmc.Geometry([period])
    model.geometry = geometry

    # Define run settings
    settings = openmc.Settings()
    settings.run_mode = 'fixed source'
    settings.particles = num_parts
    settings.batches = 1

    # Define neutron source
    mesh = openmc.UnstructuredMesh(source_mesh, 'moab')
    src = openmc.IndependentSource()
    src.space = openmc.stats.MeshSpatial(
        mesh, strengths = strengths, volume_normalized = False
    )
    src.angle = openmc.stats.Isotropic()
    src.energy = openmc.stats.Discrete([14.1e6], [1.0])
    settings.source = [src]

    # Track surface crossings
    settings.surf_source_write = {
        'surface_ids': [1],
        'max_particles': num_parts
    }

    model.settings = settings

    model.run()


def min_problem(theta, vmec, wall_s, phi, pt):
    """Minimization problem to solve for the poloidal angle.

    Arguments:
        theta (float): poloidal angle (rad).
        vmec (object): plasma equilibrium object.
        wall_s (float): closed flux surface label extrapolation at wall.
        phi (float): toroidal angle (rad).
        pt (array of float): Cartesian coordinates of interest (cm).

    Returns:
        diff (float): L2 norm of difference between coordinates of interest and
            computed point (cm).
    """
    # Compute first wall point
    fw_pt = np.array(vmec.vmec2xyz(wall_s, theta, phi))
    m2cm = 100
    fw_pt = fw_pt*m2cm
    
    diff = np.linalg.norm(pt - fw_pt)

    return diff


def find_coords(vmec, wall_s, phi, pt):
    """Solves for poloidal angle of plasma equilibrium corresponding to
    specified Cartesian coordinates.

    Arguments:
        vmec (object): plasma equilibrium object.
        wall_s (float): closed flux surface label extrapolation at wall.
        phi (float): toroidal angle (rad).
        pt (array of float): Cartesian coordinates of interest (cm).

    Returns:
        theta (float): poloidal angle (rad).
    """
    from scipy.optimize import direct

    # Solve for the poloidal angle via minimization
    theta = direct(
        min_problem,
        bounds = [(0., 2*np.pi)],
        args = (vmec, wall_s, phi, pt)
    )
    # Extract angle
    theta = theta.x

    return theta


def flux_coords(vmec, wall_s, pt):
    """Computes flux-coordinate toroidal and poloidal angles corresponding to
    specified Cartesian coordinates.
    
    Arguments:
        vmec (object): plasma equilibrium object.
        wall_s (float): closed flux surface label extrapolation at wall.
        pt (array of float): Cartesian coordinates of interest (cm).

    Returns:
        phi (float): toroidal angle (rad).
        theta (float): poloidal angle (rad).
    """
    # Extract Cartesian coordinates of original point
    x, y, z = pt
    
    phi = np.arctan2(y, x)
    theta = find_coords(vmec, wall_s, phi, pt)

    return phi, theta


def extract_coords(source_file):
    """Extracts Cartesian coordinates of particle surface crossings given an
    OpenMC surface source file.

    Arguments:
        source_file (str): path to OpenMC surface source file.
    
    Returns:
        coords (array of array of float): Cartesian coordinates of all particle
            surface crossings.
    """
    import h5py
    
    # Load source file
    file = h5py.File(source_file, 'r')
    # Extract source information
    dataset = file['source_bank']
    # Extract coordinates of particle crossings
    coords = dataset['r']

    return coords


def plot(NWL_mat, phi_bins, theta_bins, num_levels):
    """Generates contour plot of NWL.

    Arguments:
        NWL_mat (array of array of float): NWL solutions at centroids of
            (phi, theta) bins (MW).
        phi_bins (array of float): centroids of toroidal angle bins (rad).
        theta_bins (array of float): centroids of poloidal angle bins (rad).
        num_levels (int): number of contour regions.
    """
    import matplotlib.pyplot as plt
    
    phi_bins = np.rad2deg(phi_bins)
    theta_bins = np.rad2deg(theta_bins)

    levels = np.linspace(np.min(NWL_mat), np.max(NWL_mat), num = num_levels)
    fig, ax = plt.subplots()
    CF = ax.contourf(phi_bins, theta_bins, NWL_mat, levels = levels)
    cbar = plt.colorbar(CF)
    cbar.ax.set_ylabel('NWL (MW)')
    plt.xlabel('Toroidal Angle (degrees)')
    plt.ylabel('Poloidal Angle (degrees)')
    fig.savefig('NWL.png')


def NWL_plot(
    source_file, ss_file, plas_eq, tor_ext, pol_ext, wall_s, num_phi = 101, num_theta = 101, num_levels = 10
    ):
    """Computes and plots NWL.

    Arguments:
        source_file (str): path to OpenMC surface source file.
        ss_file (str): source strength input file.
        plas_eq (str): path to plasma equilibrium NetCDF file.
        tor_ext (float): toroidal extent of model (deg).
        pol_ext (float): poloidal extent of model (deg).
        wall_s (float): closed flux surface label extrapolation at wall.
        num_phi (int): number of toroidal angle bins (defaults to 101).
        num_theta (int): number of poloidal angle bins (defaults to 101).
        num_levels (int): number of contour regions (defaults to 10).
    """
    import read_vmec
    
    tor_ext = np.deg2rad(tor_ext)
    pol_ext = np.deg2rad(pol_ext)
    
    coords = extract_coords(source_file)
    
    # Load plasma equilibrium data
    vmec = read_vmec.vmec_data(plas_eq)

    phi_bins = np.linspace(0.0, tor_ext, num = num_phi)
    theta_bins = np.linspace(-pol_ext/2, pol_ext/2, num = num_theta)
    
    # Initialize count matrix
    count_mat = np.zeros((num_phi, num_theta))

    for pt in coords:
        # Extract Cartesian coordinates and format as array
        pt = [pt['x'], pt['y'], pt['z']]
        pt = np.array(pt)
        
        phi, theta = flux_coords(vmec, wall_s, pt)
        
        # Shift angles to fit in bins
        if theta > pol_ext/2:
            theta = theta - pol_ext

        for i, phi_bin in enumerate(phi_bins):
            # Conditionally contribute to bin crossing count if crossing within
            # bin
            if np.abs(phi - phi_bin) <= tor_ext/(num_phi - 1)/2:
                for j, theta_bin in enumerate(theta_bins):
                    # Conditionally contribute to bin crossing count if
                    # crossing within bin
                    if np.abs(theta - theta_bin) <= pol_ext/(num_theta - 1)/2:
                        count_mat[i,j] += 1

    # Define fusion neutron energy (eV)
    n_energy = 14.1e6
    # Define eV to joules constant
    eV2J = 1.60218e-19
    # Compute total neutron source strength (n/s)
    strengths = extract_ss(ss_file)
    SS = sum(strengths)
    # Define joules to megajoules constant
    J2MJ = 1e-6
    # Define number of source particles
    num_parts = len(coords)

    NWL_mat = count_mat*n_energy*eV2J*SS*J2MJ/num_parts

    plot(NWL_mat, phi_bins, theta_bins, num_levels)
