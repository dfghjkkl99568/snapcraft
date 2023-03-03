# -*- Mode:Python; indent-tabs-mode:nil; tab-width:4 -*-
# pylint: disable=line-too-long,too-many-lines,attribute-defined-outside-init
#
# Copyright 2020-2022 Canonical Ltd.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 3 as
# published by the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

"""The kernel plugin for building kernel snaps.

The following kernel-specific options are provided by this plugin:

    - kernel-kdefconfig:
      (list of kdefconfigs)
      defconfig target to use as the base configuration. default: "defconfig"

    - kernel-kconfigfile:
      (filepath; default: none)
      path to file to use as base configuration. If provided this option wins
      over everything else. default: None

    - kernel-kconfigflavour:
      (string; default: none)
      Ubuntu config flavour to use as base configuration. If provided this
      option wins over kernel-kdefconfig. default: None

    - kernel-kconfigs:
      (list of strings; default: none)
      explicit list of configs to force; this will override the configs that
      were set as base through kernel-kdefconfig and kernel-kconfigfile and dependent configs
      will be fixed using the defaults encoded in the kbuild config
      definitions.  If you don't want default for one or more implicit configs
      coming out of these, just add them to this list as well.

    - kernel-image-target:
      (yaml object, string or null for default target)
      the default target is bzImage and can be set to any specific
      target.
      For more complex cases where one would want to use
      the same snapcraft.yaml to target multiple architectures a
      yaml object can be used. This yaml object would be a map of
      debian architecture and kernel image build targets.

    - kernel-with-firmware:
      (boolean; default: True)
      use this flag to disable shipping binary firmwares.

    - kernel-device-trees:
      (array of string; default: none)
      list of device trees to build, the format is <device-tree-name>.dts.

    - kernel-build-efi-image
      Optional, true if we want to create an EFI image, false otherwise (false
      by default).

    - kernel-compiler
      (string; default: none)
      Optional, define compiler to use, by default gcc compiler is used.
      Other permitted compilers: clang

    - kernel-compiler-paths
      (array of strings; default: none)
      Optional, define the compiler path to be added to the PATH.
      Path is relative to the stage directory.
      Default value is empty.

    - kernel-compiler-parameters
      (array of string)
      Optional, define extra compiler parameters to be passed to the compiler.
      Default value is empty.

    - kernel-enable-zfs-support
      (boolean; default: False)
      use this flag to build in zfs support through extra ko modules

    - kernel-enable-perf
      (boolean; default: False)
      use this flag to build the perf binary

    - kernel-initrd-modules:
      (array of string; default: none)
      list of modules to include in initrd.
      Note that kernel snaps do not provide the core boot logic which comes from snappy
      Ubuntu Core OS snap. Include all modules you need for mounting rootfs here.
      If installed module(s) have any dependencies, those are automatically installed.

    - kernel-initrd-configured-modules:
      (array of string; default: none)
      list of modules to be added to the initrd
      /lib/modules-load.d/ubuntu-core-initramfs.conf config
      to be automatically loaded.
      Configured modules are automatically added to kernel-initrd-modules.
      If module in question is not supported by the kernel, it is ignored.

    - kernel-initrd-stage-firmware:
      (boolean; default: False)
      When building initrd, required firmware is automatically added based
      on the included kernel modules. By default required firmware is searched
      in the install directory of the current part. This flag allows used of firmware
      from stage directory instead.

    - kernel-initrd-firmware:
      (array of string; default: none)
      list of firmware files to be included in the initrd; these need to be
      relative paths to stage directory.
      <stage/part install dir>/firmware/* -> initrd:/lib/firmware/*

    - kernel-initrd-compression:
      (string; default: as defined in ubuntu-core-initrd(zstd)
      initrd compression to use; the only supported values now are
      'lz4', 'xz', 'gz', 'zstd'.

    - kernel-initrd-compression-options:
      Optional list of parameters to be passed to compressor used for initrd
      (array of string): defaults are
        gz:  -7
        lz4: -9 -l
        xz:  -7
        zstd: -1 -T0

    - kernel-initrd-overlay
      (string; default: none)
      Optional overlay to be applied to built initrd.
      This option is designed to provide easy way to apply initrd overlay for
      cases modifies initrd scripts for pre uc20 initrds.
      Value is relative path, in stage directory. and related part needs to be
      built before initrd part. During build it will be expanded to
      ${CRAFT_STAGE}/{initrd-overlay}
      Default: none

    - kernel-initrd-addons
      (array of string; default: none)
      Optional list of files to be added to the initrd.
      Function is similar to kernel-initrd-overlay, only it works on per file
      selection without a need to have overlay in dedicated directory.
      This option is designed to provide easy way to add additional content
      to initrd for cases like full disk encryption support, when device
      specific hook needs to be added to the initrd.
      Values are relative path from stage directory, so related part(s)
      need to be built before kernel part.
      During build it will be expanded to
      ${CRAFT_STAGE}/{initrd-addon}
      Default: none

    - kernel-add-ppa
      (boolean; default: True)
      controls if the snappy-dev PPA should be added to the system

    - kernel-use-llvm
      (boolean or string to specify version suffix; default: False)
      Use the LLVM substitutes for the GNU binutils utilities. Set this to a
      string (e.g. "-12") to use a specific version of the LLVM utilities.

This plugin support cross compilation, for which plugin expects
the build-environment is comfigured accordingly and has foreign architectures
setup accordingly.
"""

import logging
import os
import re
import subprocess
from typing import Any, Dict, List, Optional, Set, Union, cast

from craft_parts import infos, plugins
from overrides import overrides
from pydantic import root_validator

from snapcraft_legacy.plugins.v2 import _kernel_build

logger = logging.getLogger(__name__)

_SNAPD_SNAP_NAME = "snapd"
_SNAPD_SNAP_FILE = "{snap_name}_{architecture}.snap"
_SNAPPY_DEV_KEY_FINGERPRINT = "F1831DDAFC42E99D"

_default_kernel_image_target = {
    "amd64": "bzImage",
    "i386": "bzImage",
    "armhf": "zImage",
    "arm64": "Image.gz",
    "powerpc": "uImage",
    "ppc64el": "vmlinux.strip",
    "s390x": "bzImage",
    "riscv64": "Image",
}


class KernelPluginProperties(plugins.PluginProperties, plugins.PluginModel):
    """The part properties used by the Kernel plugin."""

    kernel_kdefconfig: List[str] = ["defconfig"]
    kernel_kconfigfile: Optional[str]
    kernel_kconfigflavour: Optional[str]
    kernel_kconfigs: Optional[List[str]]
    kernel_image_target: Any
    kernel_with_firmware: bool = True
    kernel_device_trees: Optional[List[str]]
    kernel_build_efi_image: bool = False
    kernel_compiler: Optional[str]
    kernel_compiler_paths: Optional[List[str]]
    kernel_compiler_parameters: Optional[List[str]]
    kernel_initrd_modules: Optional[List[str]]
    kernel_initrd_configured_modules: Optional[List[str]]
    kernel_initrd_stage_firmware: bool = False
    kernel_initrd_firmware: Optional[List[str]]
    kernel_initrd_compression: Optional[str]
    kernel_initrd_compression_options: Optional[List[str]]
    kernel_initrd_overlay: Optional[str]
    kernel_initrd_addons: Optional[List[str]]
    kernel_enable_zfs_support: bool = False
    kernel_enable_perf: bool = False
    kernel_add_ppa: bool = True
    kernel_use_llvm: Union[bool, str] = False

    # part properties required by the plugin
    @root_validator
    @classmethod
    def validate_pluging_options(cls, values):
        """If kernel-image-target is defined, it has to be string of dictionary."""
        if values.get("kernel_image_target"):
            if not isinstance(values.get("kernel_image_target"), str):
                if not isinstance(values.get("kernel_image_target"), dict):
                    raise ValueError(
                        f'kernel-image-target is in invalid format(type{type(values.get("kernel_image_target"))}). It should be either string or dictionary.'
                    )

        if values.get("kernel_initrd_compression_options") and not values.get(
            "kernel_initrd_compression"
        ):
            raise ValueError(
                "kernel-initrd-compression-options requires also kernel-initrd-compression to be defined."
            )
        return values

    @classmethod
    def unmarshal(cls, data: Dict[str, Any]):
        """Populate class attributes from the part specification.

        :param data: A dictionary containing part properties.

        :return: The populated plugin properties data object.

        :raise pydantic.ValidationError: If validation fails.
        """
        plugin_data = plugins.extract_plugin_properties(
            data, plugin_name="kernel", required=[]
        )
        return cls(**plugin_data)


# pylint: disable-next=too-many-instance-attributes
class KernelPlugin(plugins.Plugin):
    """Plugin for the kernel snap build."""

    properties_class = KernelPluginProperties

    def __init__(
        self, *, properties: plugins.PluginProperties, part_info: infos.PartInfo
    ) -> None:
        super().__init__(properties=properties, part_info=part_info)
        self.options = cast(KernelPluginProperties, self._options)

        target_arch = self._part_info.target_arch
        self._deb_arch = _kernel_build.get_deb_architecture(target_arch)
        self._kernel_arch = _kernel_build.get_kernel_architecture(target_arch)
        self._target_arch = target_arch

        # check if we are cross building
        self._cross_building = False
        if (
            self._part_info.project_info.host_arch
            != self._part_info.project_info.target_arch
        ):
            self._cross_building = True
        self._llvm_version = self._determine_llvm_version()
        self._target_arch = self._part_info.target_arch

    def _determine_llvm_version(self) -> Optional[str]:
        if (
            isinstance(self.options.kernel_use_llvm, bool)
            and self.options.kernel_use_llvm
        ):
            return "1"
        if isinstance(self.options.kernel_use_llvm, str):
            suffix = re.match(r"^-\d+$", self.options.kernel_use_llvm)
            if suffix is None:
                raise ValueError(
                    f'kernel-use-llvm must match the format "-<version>" (e.g. "-12"), not "{self.options.kernel_use_llvm}"'
                )
            return self.options.kernel_use_llvm
        # Not use LLVM utilities
        return None

    def _init_build_env(self) -> None:
        # first get all the architectures, new v2 plugin is making life difficult
        logger.info("Initializing build env...")

        self._make_cmd = ["make", "-j$(nproc)"]
        # we are building out of tree, configure paths
        self._make_cmd.append("-C")
        self._make_cmd.append("${KERNEL_SRC}")
        self._make_cmd.append("O=${CRAFT_PART_BUILD}")

        self._check_cross_compilation()
        self._set_kernel_targets()
        self._set_llvm()

        # determine type of initrd
        snapd_snap_file_name = _SNAPD_SNAP_FILE.format(
            snap_name=_SNAPD_SNAP_NAME,
            architecture=self._target_arch,
        )

        self._snapd_snap = os.path.join("${CRAFT_PART_BUILD}", snapd_snap_file_name)

    def _check_cross_compilation(self) -> None:
        if self._cross_building:
            self._make_cmd.append(f"ARCH={self._kernel_arch}")
            self._make_cmd.append("CROSS_COMPILE=${CRAFT_ARCH_TRIPLET}-")

    def _set_kernel_targets(self) -> None:
        if not self.options.kernel_image_target:
            self.kernel_image_target = _default_kernel_image_target[self._deb_arch]
        elif isinstance(self.options.kernel_image_target, str):
            self.kernel_image_target = self.options.kernel_image_target
        elif self._deb_arch in self.options.kernel_image_target:
            self.kernel_image_target = self.options.kernel_image_target[self._deb_arch]

        self._make_targets = [self.kernel_image_target, "modules"]
        self._make_install_targets = [
            "modules_install",
            "INSTALL_MOD_STRIP=1",
            "INSTALL_MOD_PATH=${CRAFT_PART_INSTALL}",
        ]
        if self.options.kernel_device_trees:
            self.dtbs = [f"{i}.dtb" for i in self.options.kernel_device_trees]
            if self.dtbs:
                self._make_targets.extend(self.dtbs)
        elif self._kernel_arch in ("arm", "arm64", "riscv64"):
            self._make_targets.append("dtbs")
            self._make_install_targets.extend(
                ["dtbs_install", "INSTALL_DTBS_PATH=${CRAFT_PART_INSTALL}/dtbs"]
            )
        self._make_install_targets.extend(self._get_fw_install_targets())

    def _set_llvm(self) -> None:
        if self._llvm_version is not None:
            self._make_cmd.append(f'LLVM="{self._llvm_version}"')

    def _get_fw_install_targets(self) -> List[str]:
        if not self.options.kernel_with_firmware:
            return []

        return [
            "firmware_install",
            "INSTALL_FW_PATH=${CRAFT_PART_INSTALL}/lib/firmware",
        ]

    def _make_initrd_cmd(
        self, initrd_overlay: Optional[str], install_dir: str, stage_dir: str
    ) -> List[str]:
        cmd_echo = [
            'echo "Generating initrd with ko modules for kernel release: ${KERNEL_RELEASE}"',
        ]

        cmd_prepare_modules_feature = [
            # install required modules to initrd
            'echo "Installing ko modules to initrd..."',
            'install_modules=""',
            'echo "Gathering module dependencies..."',
            'install_modules=""',
            "uc_initrd_feature_kernel_modules=${UC_INITRD_DEB}/usr/lib/ubuntu-core-initramfs/kernel-modules",
            "mkdir -p ${uc_initrd_feature_kernel_modules}",
            "initramfs_ko_modules_conf=${uc_initrd_feature_kernel_modules}/extra-modules.conf",
            " ".join(
                [
                    "touch",
                    "${initramfs_ko_modules_conf}",
                ]
            ),
            " ".join(
                [
                    "for",
                    "m",
                    "in",
                    "${initrd_installed_kernel_modules}",
                    "${initrd_configured_kernel_modules}",
                ]
            ),
            "do",
            " ".join(["\techo", "${m}", ">>", "${initramfs_ko_modules_conf}"]),
            "done",
            " ".join(
                [
                    "[",
                    "-e",
                    "${initramfs_ko_modules_conf}",
                    "]",
                    "&&",
                    "sort",
                    "-fu",
                    "${initramfs_ko_modules_conf} -o ${initramfs_ko_modules_conf}",
                ],
            ),
        ]

        cmd_prepare_modules_feature.extend(
            [
                'echo "Configuring ubuntu-core-initramfs.conf with supported modules"',
                'echo "If module is not included in initrd, do not include it"',
                "initramfs_conf_dir=${uc_initrd_feature_kernel_modules}/usr/lib/modules-load.d",
                "mkdir -p ${initramfs_conf_dir}",
                "initramfs_conf=${initramfs_conf_dir}/ubuntu-core-initramfs.conf",
                'echo "# configures modules" > ${initramfs_conf}',
                " ".join(
                    [
                        "for",
                        "m",
                        "in",
                        "${initrd_configured_kernel_modules}",
                    ]
                ),
                "do",
                " ".join(
                    [
                        "\tif [",
                        "-n",
                        f'"$(modprobe -n -q --show-depends -d {install_dir} -S "${{KERNEL_RELEASE}}" ${{m}})"',
                        "]; then",
                    ]
                ),
                "\t\techo ${m} >> ${initramfs_conf}",
                "\tfi",
                "done",
            ]
        )

        cmd_prepare_initrd_overlay_feature = [
            "uc_initrd_feature_firmware=${UC_INITRD_DEB}/usr/lib/ubuntu-core-initramfs/uc-firmware",
            "mkdir -p ${uc_initrd_feature_firmware}",
            "uc_initrd_feature_overlay=${UC_INITRD_DEB}/usr/lib/ubuntu-core-initramfs/uc-overlay",
            "mkdir -p ${uc_initrd_feature_overlay}",
            "",
        ]

        # gather firmware files
        if self.options.kernel_initrd_firmware:
            cmd_prepare_initrd_overlay_feature.extend(
                [
                    'echo "Installing initrd overlay firmware..."',
                    f"for f in {' '.join(self.options.kernel_initrd_firmware)}",
                    "do",
                    # firmware can be from kernel build or from stage
                    # firmware from kernel build takes preference
                    " ".join(
                        [
                            "\tif !",
                            "link_files",
                            f'"{install_dir}"',
                            '"${f}"',
                            '"${uc_initrd_feature_firmware}/lib"',
                            ";",
                            "then",
                        ]
                    ),
                    " ".join(
                        [
                            "\t\tif !",
                            "link_files",
                            f'"{stage_dir}"',
                            '"${f}"',
                            '"${uc_initrd_feature_firmware}/lib"',
                            ";",
                            "then",
                        ]
                    ),
                    '\t\t\techo "Missing firmware [${f}], ignoring it"',
                    "\t\tfi",
                    "\tfi",
                    "done",
                    "",
                ]
            )

        # apply overlay if defined
        if initrd_overlay:
            cmd_prepare_initrd_overlay_feature.extend(
                [
                    " ".join(
                        [
                            "link_files",
                            f'"{stage_dir}/{initrd_overlay}"',
                            '""',
                            '"${uc_initrd_feature_overlay}"',
                        ]
                    ),
                    "",
                ]
            )

        # apply overlay addons if defined
        if self.options.kernel_initrd_addons:
            cmd_prepare_initrd_overlay_feature.extend(
                [
                    'echo "Installing initrd addons..."',
                    f"for a in {' '.join(self.options.kernel_initrd_addons)}",
                    "do",
                    '\techo "Copy overlay: ${a}"',
                    " ".join(
                        [
                            "\tlink_files",
                            f'"{stage_dir}"',
                            '"${a}"',
                            '"${uc_initrd_feature_overlay}"',
                        ]
                    ),
                    "done",
                ],
            )

        cmd_prepare_snap_bootstrap_feature = [
            # install selected snap bootstrap
            'echo "Preparing snap-boostrap initrd feature..."',
            "uc_initrd_feature_snap_bootstratp=${UC_INITRD_DEB}/usr/lib/ubuntu-core-initramfs/snap-bootstrap",
            "mkdir -p ${uc_initrd_feature_snap_bootstratp}",
            " ".join(
                [
                    "link_files",
                    '"${SNAPD_UNPACKED_SNAP}"',
                    '"usr/lib/snapd/snap-bootstrap"',
                    '"${uc_initrd_feature_snap_bootstratp}"',
                ]
            ),
            " ".join(
                [
                    "link_files",
                    '"${SNAPD_UNPACKED_SNAP}"',
                    '"usr/lib/snapd/info"',
                    '"${uc_initrd_feature_snap_bootstratp}"',
                ]
            ),
            " ".join(
                [
                    "cp",
                    "${SNAPD_UNPACKED_SNAP}/usr/lib/snapd/info",
                    f"{install_dir}/snapd-info",
                ]
            ),
        ]

        cmd_create_initrd = [
            f"if compgen -G {install_dir}/initrd.img* > /dev/null; then",
            f"\trm -rf {install_dir}/initrd.img*",
            "fi",
        ]

        cmd_create_initrd.extend(
            [
                "",
                "",
                " ".join(
                    [
                        "ubuntu_core_initramfs=${UC_INITRD_DEB}/usr/bin/ubuntu-core-initramfs"
                    ]
                ),
            ],
        )

        # ubuntu-core-initramfs does not support configurable compression command
        # we still want to support this as configurable option though.
        comp_command = _kernel_build.compression_cmd(
            initrd_compression=self.options.kernel_initrd_compression,
            initrd_compression_options=self.options.kernel_initrd_compression_options,
        )
        if comp_command:
            cmd_create_initrd.extend(
                [
                    "",
                    'echo "Updating compression command to be used for initrd"',
                    " ".join(
                        [
                            "sed",
                            "-i",
                            f"'s/zstd -1 -T0/{comp_command}/g'",
                            "${ubuntu_core_initramfs}",
                        ],
                    ),
                ]
            )
        cmd_create_initrd.extend(
            [
                'echo "Workaround for bug in ubuntu-core-initramfs"',
                " ".join(
                    [
                        "for",
                        "feature",
                        "in",
                        "kernel-modules",
                        "snap-bootstrap",
                        "uc-firmware",
                        "uc-overlay",
                    ],
                ),
                "do",
                " ".join(
                    [
                        "\tlink_files",
                        '"${UC_INITRD_DEB}/usr/lib/ubuntu-core-initramfs/${feature}"',
                        '"*"',
                        '"${UC_INITRD_DEB}/usr/lib/ubuntu-core-initramfs/main"',
                    ],
                ),
                "done",
                " ".join(
                    [
                        "cp",
                        "${initramfs_ko_modules_conf}",
                        "${UC_INITRD_DEB}/usr/lib/ubuntu-core-initramfs/modules/main/extra-modules.conf",
                    ],
                ),
                "",
            ],
        )
        firmware_dir = f"{install_dir}/lib/firmware"
        if self.options.kernel_initrd_stage_firmware:
            firmware_dir = f"{stage_dir}/firmware"
        cmd_create_initrd.extend(
            [
                "",
                " ".join(
                    [
                        f'[ ! -d "{firmware_dir}" ]',
                        "&&",
                        f'echo -e "firmware directory {firmware_dir} does not exist, consider using'
                        ' kernel-initrd-stage-firmware: true/false option"',
                        "&&",
                        "exit 1",
                    ]
                ),
                "",
                " ".join(
                    [
                        "${ubuntu_core_initramfs}",
                        "create-initrd",
                        "--kernelver=${KERNEL_RELEASE}",
                        "--kerneldir",
                        f"{install_dir}/lib/modules/${{KERNEL_RELEASE}}",
                        "--firmwaredir",
                        firmware_dir,
                        "--skeleton",
                        "${UC_INITRD_DEB}/usr/lib/ubuntu-core-initramfs",
                        # "--feature",
                        # "kernel-modules",
                        # "snap-bootstrap",
                        # "uc-firmware",
                        # "uc-overlay",
                        "--output",
                        f"{install_dir}/initrd.img",
                    ],
                ),
                f"ln $(ls {install_dir}/initrd.img*) {install_dir}/initrd.img",
            ]
        )
        if self.options.kernel_build_efi_image:
            cmd_create_initrd.extend(
                [
                    "",
                    'echo "Building kernel.efi"',
                    "stub_p=$(find ${UC_INITRD_DEB}/usr/lib/ubuntu-core-initramfs/efi/ -maxdepth 1 -name 'linux*.efi.stub' -printf '%f\\n')",
                    " ".join(
                        [
                            "${ubuntu_core_initramfs}",
                            "create-efi",
                            "--kernelver=${KERNEL_RELEASE}",
                            "--stub",
                            "${UC_INITRD_DEB}/usr/lib/ubuntu-core-initramfs/efi/${stub_p}",
                            "--sbat",
                            "${UC_INITRD_DEB}/usr/lib/ubuntu-core-initramfs/efi/sbat.txt",
                            "--key",
                            "${UC_INITRD_DEB}/usr/lib/ubuntu-core-initramfs/snakeoil/PkKek-1-snakeoil.key",
                            "--cert",
                            "${UC_INITRD_DEB}/usr/lib/ubuntu-core-initramfs/snakeoil/PkKek-1-snakeoil.pem",
                            "--initrd",
                            f"{install_dir}/initrd.img",
                            "--kernel",
                            f"{install_dir}/${{KERNEL_IMAGE_TARGET}}",
                            "--output",
                            f"{install_dir}/kernel.efi",
                        ],
                    ),
                    f"ln $(ls {install_dir}/kernel.efi*) {install_dir}/kernel.efi",
                ],
            )

        return [
            *cmd_echo,
            *cmd_prepare_modules_feature,
            "",
            *cmd_prepare_initrd_overlay_feature,
            "",
            *cmd_prepare_snap_bootstrap_feature,
            "",
            'echo "Create new initrd..."',
            *cmd_create_initrd,
        ]

    def _configure_compiler(self) -> None:
        # check if we are using gcc or another compiler
        if self.options.kernel_compiler:
            # at the moment only clang is supported as alternative, warn otherwise
            kernel_compiler = re.match(r"^clang(-\d+)?$", self.options.kernel_compiler)
            if kernel_compiler is None:
                logger.warning("Only other 'supported' compiler is clang")
                logger.info("hopefully you know what you are doing")
            self._make_cmd.append(f'CC="{self.options.kernel_compiler}"')
        if self.options.kernel_compiler_parameters:
            for opt in self.options.kernel_compiler_parameters:
                self._make_cmd.append(str(opt))

    @overrides
    def get_build_snaps(self) -> Set[str]:
        return set()

    def _add_snappy_ppa(self) -> None:
        # Add ppa necessary to build initrd.
        # TODO: reimplement once snapcraft allows to the plugins
        # to add custom ppa.
        # For the moment we need to handle this as part of the
        # get_build_packages() call and add ppa manually.

        # Building of the initrd requires custom tools available in
        # ppa:snappy-dev/image.

        proc = subprocess.run(
            ["grep", "-r", "snappy-dev/image/ubuntu", "/etc/apt/sources.list.d/"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )

        if (
            not proc.stdout
            or proc.stdout.decode().find("snappy-dev/image/ubuntu") == -1
        ):
            # check if we need to import key
            try:
                proc = subprocess.run(
                    ["apt-key", "export", _SNAPPY_DEV_KEY_FINGERPRINT],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    check=True,
                )
            except subprocess.CalledProcessError as error:
                # Export shouldn't exit with failure based on testing
                raise ValueError(
                    f"error to check for key={_SNAPPY_DEV_KEY_FINGERPRINT}: {error.output}"
                ) from error

            apt_key_output = proc.stdout.decode()
            if "BEGIN PGP PUBLIC KEY BLOCK" in apt_key_output:
                logger.info("key for ppa:snappy-dev/image already imported")

            if "nothing exported" in apt_key_output:
                logger.info("importing key for ppa:snappy-dev/image")
                # first import key for the ppa
                try:
                    subprocess.run(
                        [
                            "apt-key",
                            "adv",
                            "--keyserver",
                            "keyserver.ubuntu.com",
                            "--recv-keys",
                            _SNAPPY_DEV_KEY_FINGERPRINT,
                        ],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        check=True,
                    )
                except subprocess.CalledProcessError as error:
                    raise ValueError(
                        f"Failed to add ppa key: {_SNAPPY_DEV_KEY_FINGERPRINT}: {error.output}"
                    ) from error

            # add ppa itself
            logger.warning("adding ppa:snappy-dev/image to handle initrd builds")
            subprocess.run(
                ["add-apt-repository", "-y", "ppa:snappy-dev/image"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=True,
            )

    @overrides
    def get_build_packages(self) -> Set[str]:
        build_packages = {
            "bc",
            "binutils",
            "gcc",
            "cmake",
            "cryptsetup",
            "dracut-core",
            "kmod",
            "kpartx",
            "systemd",
        }
        # install correct initramfs compression tool
        if self.options.kernel_initrd_compression == "lz4":
            build_packages |= {"lz4"}
        elif self.options.kernel_initrd_compression == "xz":
            build_packages |= {"xz-utils"}
        elif (
            not self.options.kernel_initrd_compression
            or self.options.kernel_initrd_compression == "zstd"
        ):
            build_packages |= {"zstd"}

        if self.options.kernel_enable_zfs_support:
            build_packages |= {
                "autoconf",
                "automake",
                "libblkid-dev",
                "libtool",
                "python3",
            }

        # for cross build of zfs we also need libc6-dev:<target arch>
        if self.options.kernel_enable_zfs_support and self._cross_building:
            build_packages |= {f"libc6-dev:{self._target_arch}"}

        if self.options.kernel_build_efi_image:
            build_packages |= {"llvm"}

        # add snappy ppa to get correct initrd tools
        if self.options.kernel_add_ppa:
            self._add_snappy_ppa()

        if self._llvm_version is not None:
            # Use the specified version suffix for the packages if it has been
            # set by the user
            suffix = self._llvm_version if self._llvm_version != "1" else ""
            llvm_packages = [
                "llvm",
                "lld",
            ]
            build_packages |= {f"{f}{suffix}" for f in llvm_packages}

        return build_packages

    @overrides
    def get_build_environment(self) -> Dict[str, str]:
        logger.info("Getting build env...")
        self._init_build_env()

        env = {
            "CROSS_COMPILE": "${CRAFT_ARCH_TRIPLET}-",
            "ARCH": self._kernel_arch,
            "DEB_ARCH": "${CRAFT_TARGET_ARCH}",
            "UC_INITRD_DEB": "${CRAFT_PART_BUILD}/ubuntu-core-initramfs",
            "SNAPD_UNPACKED_SNAP": "${CRAFT_PART_BUILD}/unpacked_snapd",
            "KERNEL_BUILD_ARCH_DIR": f"${{CRAFT_PART_BUILD}}/arch/{self._kernel_arch}/boot",
            "KERNEL_IMAGE_TARGET": self.kernel_image_target,
        }

        # check if there is custom path to be included
        if self.options.kernel_compiler_paths:
            custom_paths = [
                os.path.join("${CRAFT_STAGE}", f)
                for f in self.options.kernel_compiler_paths
            ]
            path = custom_paths + [
                "${PATH}",
            ]
            env["PATH"] = ":".join(path)

        return env

    def _get_post_install_cmd(
        self,
        initrd_overlay: Optional[str],
        build_dir: str,
        install_dir: str,
        stage_dir: str,
    ) -> List[str]:
        return [
            "",
            *_kernel_build.parse_kernel_release_cmd(build_dir=build_dir),
            "",
            *_kernel_build.copy_vmlinuz_cmd(install_dir=install_dir),
            "",
            *_kernel_build.copy_system_map_cmd(
                build_dir=build_dir, install_dir=install_dir
            ),
            "",
            *_kernel_build.copy_dtbs_cmd(
                kernel_device_trees=self.options.kernel_device_trees,
                install_dir=install_dir,
            ),
            "",
            *self._make_initrd_cmd(
                initrd_overlay=initrd_overlay,
                install_dir=install_dir,
                stage_dir=stage_dir,
            ),
            "",
        ]

    def _get_install_command(
        self,
        initrd_overlay: Optional[str],
        build_dir: str,
        install_dir: str,
        stage_dir: str,
    ) -> List[str]:
        # install to installdir
        make_cmd = self._make_cmd.copy()
        make_cmd += [
            f"CONFIG_PREFIX={install_dir}",
        ]
        make_cmd += self._make_install_targets
        cmd = [
            'echo "Installing kernel build..."',
            " ".join(make_cmd),
        ]

        # add post-install steps
        cmd.extend(
            self._get_post_install_cmd(
                initrd_overlay=initrd_overlay,
                build_dir=build_dir,
                install_dir=install_dir,
                stage_dir=stage_dir,
            ),
        )

        # install .config as config-$version
        cmd.extend(
            _kernel_build.install_config_cmd(
                build_dir=build_dir, install_dir=install_dir
            )
        )

        cmd.extend(_kernel_build.arrange_install_dir_cmd(install_dir=install_dir))

        return cmd

    @overrides
    def get_build_commands(self) -> List[str]:
        logger.info("Getting build commands...")
        self._configure_compiler()
        # kernel source can be either CRAFT_PART_SRC or CRAFT_PROJECT_DIR
        return [
            "[ -d ${CRAFT_PART_SRC}/kernel ] && KERNEL_SRC=${CRAFT_PART_SRC} || KERNEL_SRC=${CRAFT_PROJECT_DIR}",
            'echo "PATH=$PATH"',
            'echo "KERNEL_SRC=${KERNEL_SRC}"',
            "",
            *_kernel_build.get_initrd_kernel_modules(
                initrd_modules=self.options.kernel_initrd_modules,
                configured_modules=self.options.kernel_initrd_configured_modules,
            ),
            "",
            *_kernel_build.link_files_fnc_cmd(),
            "",
            *_kernel_build.download_core_initrd_fnc_cmd(),
            "",
            *_kernel_build.download_generic_initrd_cmd(target_arch=self._target_arch),
            "",
            *_kernel_build.download_snap_bootstrap_fnc_cmd(),
            "",
            *_kernel_build.download_snap_bootstrap_cmd(target_arch=self._target_arch),
            "",
            *_kernel_build.clone_zfs_cmd(
                enable_zfs=self.options.kernel_enable_zfs_support,
                dest_dir="${CRAFT_PART_BUILD}",
            ),
            "",
            *_kernel_build.clean_old_build_cmd(dest_dir="${CRAFT_PART_INSTALL}"),
            "",
            *_kernel_build.get_configure_command(
                make_cmd=self._make_cmd,
                config_file=self.options.kernel_kconfigfile,
                config_flavour=self.options.kernel_kconfigflavour,
                defconfig=self.options.kernel_kdefconfig,
                configs=self.options.kernel_kconfigs,
                dest_dir="${CRAFT_PART_BUILD}",
            ),
            "",
            *_kernel_build.call_check_config_cmd(dest_dir="${CRAFT_PART_BUILD}"),
            "",
            *_kernel_build.get_build_command(
                make_cmd=self._make_cmd, targets=self._make_targets
            ),
            *self._get_install_command(
                initrd_overlay=self.options.kernel_initrd_overlay,
                build_dir="${CRAFT_PART_BUILD}",
                install_dir="${CRAFT_PART_INSTALL}",
                stage_dir="${CRAFT_STAGE}",
            ),
            "",
            *_kernel_build.get_zfs_build_commands(
                enable_zfs=self.options.kernel_enable_zfs_support,
                arch_triplet="${CRAFT_ARCH_TRIPLET}",
                build_dir="${CRAFT_PART_BUILD}",
                install_dir="${CRAFT_PART_INSTALL}",
            ),
            "",
            *_kernel_build.get_perf_build_commands(
                make_cmd=self._make_cmd,
                enable_perf=self.options.kernel_enable_perf,
                src_dir="${CRAFT_PART_SRC}",
                build_dir="${CRAFT_PART_BUILD}",
                install_dir="${CRAFT_PART_INSTALL}",
            ),
            'echo "Kernel build finished!"',
        ]

    @classmethod
    def get_out_of_source_build(cls) -> bool:
        """Return whether the plugin performs out-of-source-tree builds."""
        return True