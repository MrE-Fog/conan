import os

from conans.cli.command import conan_command, COMMAND_GROUPS, OnceArgument
from conans.cli.commands import make_abs_path
from conans.cli.commands.export import common_args_export
from conans.cli.commands.install import _get_conanfile_path
from conans.cli.common import get_lockfile, get_profiles_from_args, _add_common_install_arguments, \
    _help_build_policies, get_multiple_remotes
from conans.cli.conan_app import ConanApp
from conans.cli.formatters.graph import print_graph_basic, print_graph_packages
from conans.cli.output import ConanOutput
from conans.client.conanfile.build import run_build_method
from conans.errors import ConanException, conanfile_exception_formatter
from conans.util.files import chdir, mkdir


@conan_command(group=COMMAND_GROUPS['creator'])
def create(conan_api, parser, *args):
    """
    Create a package
    """
    common_args_export(parser)
    _add_common_install_arguments(parser, build_help=_help_build_policies.format("never"),
                                  lockfile=False)
    parser.add_argument("--build-require", action='store_true', default=False,
                        help='The provided reference is a build-require')
    parser.add_argument("--require-override", action="append",
                        help="Define a requirement override")
    parser.add_argument("-tbf", "--test-build-folder", action=OnceArgument,
                        help='Working directory for the build of the test project.')
    parser.add_argument("-tf", "--test-folder", action=OnceArgument,
                        help='Alternative test folder name. By default it is "test_package". '
                             'Use "None" to skip the test stage')
    args = parser.parse_args(*args)

    cwd = os.getcwd()
    path = _get_conanfile_path(args.path, cwd, py=True)
    lockfile_path = make_abs_path(args.lockfile, cwd)
    lockfile = get_lockfile(lockfile=lockfile_path, strict=False)  # Create is NOT strict!
    remotes = get_multiple_remotes(conan_api, args.remote)
    profile_host, profile_build = get_profiles_from_args(conan_api, args)

    out = ConanOutput()
    out.highlight("-------- Exporting the recipe ----------")
    ref = conan_api.export.export(path=path,
                                  name=args.name, version=args.version,
                                  user=args.user, channel=args.channel,
                                  lockfile=lockfile,
                                  ignore_dirty=args.ignore_dirty)

    out.highlight("\n-------- Input profiles ----------")
    out.info("Profile host:")
    out.info(profile_host.dumps())
    out.info("Profile build:")
    out.info(profile_build.dumps())

    if args.test_folder == "None":
        # Now if parameter --test-folder=None (string None) we have to skip tests
        args.test_folder = False
    test_conanfile_path = _get_test_conanfile_path(args.test_folder, path)
    if test_conanfile_path:
        if args.build_require:
            raise ConanException("--build-require should not be specified, test_package does it")
        root_node = conan_api.graph.load_root_test_conanfile(test_conanfile_path, ref,
                                                             profile_host, profile_build,
                                                             require_overrides=args.require_override,
                                                             remotes=remotes,
                                                             update=args.update,
                                                             lockfile=lockfile)
    else:
        req_override = args.require_override
        root_node = conan_api.graph.load_root_virtual_conanfile(ref, profile_host,
                                                                is_build_require=args.build_require,
                                                                require_overrides=req_override)

    out.highlight("-------- Computing dependency graph ----------")
    check_updates = args.check_updates if "check_updates" in args else False
    deps_graph = conan_api.graph.load_graph(root_node, profile_host=profile_host,
                                            profile_build=profile_build,
                                            lockfile=lockfile,
                                            remotes=remotes,
                                            update=args.update,
                                            check_update=check_updates)
    print_graph_basic(deps_graph)
    out.highlight("\n-------- Computing necessary packages ----------")
    if args.build is None:  # Not specified, force build the tested library
        build_modes = [ref.name]
    else:
        build_modes = args.build
    conan_api.graph.analyze_binaries(deps_graph, build_modes, remotes=remotes, update=args.update)
    print_graph_packages(deps_graph)

    out.highlight("\n-------- Installing packages ----------")
    conan_api.install.install_binaries(deps_graph=deps_graph, remotes=remotes, update=args.update)

    if args.lockfile_out:
        lockfile_out = make_abs_path(args.lockfile_out, cwd)
        out.info(f"Saving lockfile: {lockfile_out}")
        lockfile.save(lockfile_out)

    if test_conanfile_path:
        out.highlight("\n-------- Testing the package ----------")

        conanfile_folder = os.path.dirname(test_conanfile_path)
        conan_api.install.install_consumer(deps_graph=deps_graph,
                                           source_folder=conanfile_folder,
                                           output_folder=conanfile_folder)
        conanfile = deps_graph.root.conanfile

        if hasattr(conanfile, "layout"):
            conanfile.folders.set_base_build(conanfile_folder)
            conanfile.folders.set_base_source(conanfile_folder)
            conanfile.folders.set_base_package(conanfile_folder)
            conanfile.folders.set_base_generators(conanfile_folder)
        else:
            conanfile.folders.set_base_build(conanfile_folder)
            conanfile.folders.set_base_source(conanfile_folder)
            conanfile.folders.set_base_package(conanfile_folder)
            conanfile.folders.set_base_generators(conanfile_folder)

        out.highlight("\n-------- Testing the package: Building ----------")
        mkdir(conanfile.build_folder)
        with chdir(conanfile.build_folder):
            app = ConanApp(conan_api.cache_folder)
            run_build_method(conanfile, app.hook_manager, conanfile_path=test_conanfile_path)

        out.highlight("\n-------- Testing the package: Running test() ----------")
        conanfile.output.highlight("Running test()")
        with conanfile_exception_formatter(conanfile, "test"):
            with chdir(conanfile.build_folder):
                conanfile.test()


def _get_test_conanfile_path(tf, conanfile_path):
    """Searches in the declared test_folder or in the standard locations"""

    if tf is False:
        # Look up for testing conanfile can be disabled if tf (test folder) is False
        return None

    test_folders = [tf] if tf else ["test_package", "test"]
    base_folder = os.path.dirname(conanfile_path)
    for test_folder_name in test_folders:
        test_folder = os.path.join(base_folder, test_folder_name)
        test_conanfile_path = os.path.join(test_folder, "conanfile.py")
        if os.path.exists(test_conanfile_path):
            return test_conanfile_path
    else:
        if tf:
            raise ConanException("test folder '%s' not available, or it doesn't have a conanfile.py"
                                 % tf)