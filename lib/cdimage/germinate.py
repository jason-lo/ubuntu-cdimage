# Copyright (C) 2013 Canonical Ltd.
# Author: Colin Watson <cjwatson@ubuntu.com>

# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; version 3 of the License.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

"""Germinate handling."""

from __future__ import print_function

from collections import OrderedDict, defaultdict
import errno
import gzip
import os
import re
import shutil
import subprocess
import traceback

from cdimage import osextras
from cdimage.log import logger
from cdimage.mail import send_mail
from cdimage.mirror import find_mirror
from cdimage.proxy import proxy_check_call

__metaclass__ = type


class GerminateNotInstalled(Exception):
    pass


class Germination:
    def __init__(self, config, prefer_vcs=True):
        self.config = config
        # Set to False to use old-style seed checkouts.
        self.prefer_vcs = prefer_vcs

    @property
    def germinate_path(self):
        paths = [
            os.path.join(self.config.root, "germinate", "bin", "germinate"),
            os.path.join(self.config.root, "germinate", "germinate.py"),
        ]
        for path in paths:
            if os.access(path, os.X_OK):
                return path
        else:
            raise GerminateNotInstalled(
                "Please check out lp:germinate in %s." %
                os.path.join(self.config.root, "germinate"))

    def output_dir(self, project):
        return os.path.join(
            self.config.root, "scratch", project, self.config.full_series,
            self.config.image_type, "germinate")

    def seed_sources(self, project):
        if self.config["LOCAL_SEEDS"]:
            return [self.config["LOCAL_SEEDS"]]
        elif self.prefer_vcs:
            bzrpattern = "http://bazaar.launchpad.net/~%s/ubuntu-seeds/"
            gitpattern = "https://git.launchpad.net/~%s/ubuntu-seeds/+git/"
            series = self.config["DIST"]
            sources = [gitpattern % "ubuntu-core-dev"]
            if project in ("kubuntu", "kubuntu-active"):
                sources.insert(0, bzrpattern % "kubuntu-dev")
            elif project == "ubuntustudio":
                sources.insert(0, gitpattern % "ubuntustudio-dev")
            elif project == "mythbuntu":
                sources.insert(0, bzrpattern % "mythbuntu-dev")
            elif project == "xubuntu":
                sources.insert(0, gitpattern % "xubuntu-dev")
            elif project in ("lubuntu", "lubuntu-next"):
                sources.insert(0, gitpattern % "lubuntu-dev")
            elif project == "ubuntu-gnome":
                sources.insert(0, gitpattern % "ubuntu-gnome-dev")
            elif project == "ubuntu-budgie":
                sources.insert(0, bzrpattern % "ubuntubudgie-dev")
            elif project == "ubuntu-mate":
                sources.insert(0, bzrpattern % "ubuntu-mate-dev")
            elif project == "ubuntukylin":
                if series >= "xenial":
                    sources.insert(0, gitpattern % "ubuntukylin-members")
                else:
                    sources.insert(0, bzrpattern % "ubuntu-core-dev")
            return sources
        else:
            return ["http://people.canonical.com/~ubuntu-archive/seeds/"]

    @property
    def use_vcs(self):
        if self.config["LOCAL_SEEDS"]:
            # Local changes may well not be committed.
            return False
        else:
            return self.prefer_vcs

    def make_index(self, project, arch, rel_target, rel_paths):
        target = os.path.join(self.output_dir(project), rel_target)
        osextras.mkemptydir(os.path.dirname(target))
        with gzip.GzipFile(target, "wb") as target_file:
            for rel_path in rel_paths:
                if os.path.isabs(rel_path):
                    abs_path = rel_path
                else:
                    abs_path = os.path.join(
                        find_mirror(self.config, arch), rel_path)
                if os.path.isfile(abs_path):
                    with gzip.GzipFile(abs_path, "rb") as source_file:
                        target_file.write(source_file.read())

    @property
    def germinate_dists(self):
        if self.config["GERMINATE_DISTS"]:
            return self.config["GERMINATE_DISTS"].split(",")
        else:
            dist_patterns = ["%s", "%s-security", "%s-updates"]
            if self.config.get("PROPOSED", "0") not in ("", "0"):
                dist_patterns.append("%s-proposed")
            return [pattern % self.config.series for pattern in dist_patterns]

    def seed_dist(self, project):
        if project == "ubuntu-server":
            return "ubuntu.%s" % self.config.series
        elif project == "ubuntukylin":
            if self.config["DIST"] >= "xenial":
                return "ubuntukylin.%s" % self.config.series
            else:
                return "ubuntu.%s" % self.config.series
        elif project == "lubuntu-next":
            return "lubuntu.%s" % self.config.series
        else:
            return "%s.%s" % (project, self.config.series)

    @property
    def components(self):
        yield "main"
        if not self.config["CDIMAGE_ONLYFREE"]:
            yield "restricted"
        if self.config["CDIMAGE_UNSUPPORTED"]:
            yield "universe"
            if not self.config["CDIMAGE_ONLYFREE"]:
                yield "multiverse"

    # TODO: convert to Germinate's native Python interface
    def germinate_arch(self, project, arch):
        cpuarch = arch.split("+")[0]

        for dist in self.germinate_dists:
            for suffix in (
                "binary-%s/Packages.gz" % cpuarch,
                "source/Sources.gz",
                "debian-installer/binary-%s/Packages.gz" % cpuarch,
            ):
                files = [
                    "dists/%s/%s/%s" % (dist, component, suffix)
                    for component in self.components]
                if self.config["LOCAL"]:
                    files.append(
                        "%s/dists/%s/local/%s" %
                        (self.config["LOCALDEBS"], dist, suffix))
                self.make_index(project, arch, files[0], files)

        arch_output_dir = os.path.join(self.output_dir(project), arch)
        osextras.mkemptydir(arch_output_dir)
        if (self.config["GERMINATE_HINTS"] and
                os.path.isfile(self.config["GERMINATE_HINTS"])):
            shutil.copy2(
                self.config["GERMINATE_HINTS"],
                os.path.join(arch_output_dir, "hints"))
        command = [
            self.germinate_path,
            "--seed-source", ",".join(self.seed_sources(project)),
            "--mirror", "file://%s/" % self.output_dir(project),
            "--seed-dist", self.seed_dist(project),
            "--dist", ",".join(self.germinate_dists),
            "--arch", cpuarch,
            "--components", "main",
            "--no-rdepends",
        ]
        if self.use_vcs:
            command.append("--vcs=auto")
        if self.config.image_type == "source":
            command.append("--always-follow-build-depends")
        proxy_check_call(
            self.config, "germinate", command, cwd=arch_output_dir,
            env=dict(os.environ, GIT_TERMINAL_PROMPT="0"))
        output_structure = os.path.join(self.output_dir(project), "STRUCTURE")
        shutil.copy2(
            os.path.join(arch_output_dir, "structure"), output_structure)

    def germinate_project(self, project):
        osextras.mkemptydir(self.output_dir(project))

        for arch in self.config.arches:
            logger.info(
                "Germinating for %s/%s ..." % (self.config.series, arch))
            self.germinate_arch(project, arch)

    def run(self):
        if self.config.image_type == "source":
            for project in self.config.all_projects:
                self.germinate_project(project)
        else:
            self.germinate_project(self.config.project)

    def output(self, project):
        if project == "source":
            # TODO cjwatson 2013-04-25: Work around layering violation.
            project = self.config.all_projects[0]
        return GerminateOutput(self.config, self.output_dir(project))


class NoMasterSeeds(Exception):
    pass


re_not_base = re.compile(
    r"^(linux-(image|restricted|amd64|386|686|k7|power|ia64|itanium|mckinley|"
    r"sparc|hppa|imx51|dove|omap).*|"
    r"nvidia-kernel-common|grub|yaboot|efibootmgr|elilo|silo|palo)$")


class GerminateOutput:
    def __init__(self, config, directory):
        self.config = config
        self.directory = directory
        self.structure = os.path.join(directory, "STRUCTURE")
        self._parse_structure()

    def _parse_structure(self):
        self._seeds = OrderedDict()
        with open(self.structure) as structure:
            for line in structure:
                line = line.strip()
                if not line or line.startswith("#") or ":" not in line:
                    continue
                seed, inherit = line.split(":", 1)
                self._seeds[seed] = inherit.split()

    def _expand_inheritance(self, seed, inherit):
        for s in self._seeds.get(seed, ()):
            self._expand_inheritance(s, inherit)
        if seed not in inherit:
            inherit.append(seed)

    def _inheritance(self, seed):
        inherit = []
        self._expand_inheritance(seed, inherit)
        return inherit

    def _without_inheritance(self, subtract, seeds):
        subtract_inherit = self._inheritance(subtract)
        remaining = set(seeds) - set(subtract_inherit)
        return [seed for seed in seeds if seed in remaining]

    def list_seeds(self, mode):
        project = self.config.project
        series = self.config["DIST"]

        if mode == "all":
            for seed in self._seeds:
                yield seed
        elif mode == "tasks":
            ship = "ship"
            if "ship-addon" in self._seeds:
                ship = "ship-addon"
            in_squashfs = None
            if project == "ubuntu-server":
                ship = "server-ship"
                in_squashfs = ["minimal"]
            elif project == "kubuntu-active":
                ship = "active-ship"
            seeds = self._inheritance(ship)
            if (self.config["CDIMAGE_SQUASHFS_BASE"] and
                    in_squashfs is not None):
                for subtract in in_squashfs:
                    seeds = self._without_inheritance(subtract, seeds)
            for seed in seeds:
                yield seed
            if self.config["CDIMAGE_DVD"]:
                # TODO cjwatson 2007-04-18: hideous hack to fix DVD tasks
                yield "dns-server"
                yield "lamp-server"
        elif mode == "installer":
            if self.config["CDIMAGE_INSTALL_BASE"]:
                yield "installer"
        elif mode == "debootstrap":
            yield "required"
            yield "minimal"
        elif mode == "base":
            yield "boot"
            yield "required"
            yield "minimal"
            yield "standard"
        elif mode == "ship-live":
            if project == "kubuntu-active":
                yield "ship-active-live"
            elif project == "lubuntu" and series == "bionic":
                yield "ship-live-gtk"
                yield "ship-live-share"
            elif project == "lubuntu-next" and series == "bionic":
                yield "ship-live-qt"
                yield "ship-live-share"
            elif project == "ubuntu-server" and series >= "bionic":
                yield "server-ship-live"
            else:
                yield "ship-live"
        elif mode == "addon":
            ship = self._inheritance("ship")
            ship_addon = self._inheritance("ship-addon")
            for seed in ship_addon:
                if seed not in ship:
                    yield seed
        elif mode == "dvd":
            if project == "edubuntu":
                # no inheritance; most of this goes on the live filesystem
                yield "dvd"
                yield "ship-live"
            elif project == "ubuntu":
                # no inheritance; most of this goes on the live filesystem
                yield "usb-langsupport"
                yield "usb-ship-live"
            elif project == "ubuntustudio":
                # no inheritance; most of this goes on the live filesystem
                yield "dvd"
                if series >= "bionic":
                    yield "ship-live"
            else:
                for seed in self._inheritance("dvd"):
                    yield seed

    def seed_path(self, arch, seed):
        return os.path.join(self.directory, arch, seed)

    def seed_packages(self, arch, seed):
        with open(self.seed_path(arch, seed)) as seed_file:
            lines = seed_file.read().splitlines()[2:-2]
            return [line.split(None, 1)[0] for line in lines]

    def master_seeds(self):
        if self.config["CDIMAGE_ADDON"]:
            for seed in self.list_seeds("addon"):
                yield seed
        elif self.config["CDIMAGE_ONLYSOURCE"]:
            for seed in self.list_seeds("all"):
                yield seed
        else:
            for seed in self.list_seeds("installer"):
                yield seed
            if self.config["CDIMAGE_DVD"]:
                for seed in self.list_seeds("dvd"):
                    if seed not in ("installer", "casper"):
                        yield seed
            elif self.config["CDIMAGE_INSTALL"]:
                for seed in self.list_seeds("tasks"):
                    if seed not in ("installer", "casper"):
                        yield seed
            else:
                if self.config.get("CDIMAGE_INSTALL_BASE") == "1":
                    for seed in self.list_seeds("base"):
                        if seed not in ("installer", "casper"):
                            yield seed
                if self.config.get("CDIMAGE_LIVE") == "1":
                    for seed in self.list_seeds("ship-live"):
                        if seed not in ("installer", "casper"):
                            yield seed

    def master_task_entries(self, project, source=False):
        series = self.config.series

        found = False
        for seed in self.master_seeds():
            # https://blueprints.launchpad.net/ubuntu/+spec/edubuntu-on-two-cds
            if (self.config["CDIMAGE_DVD"] != "1" and
                    self.config["CDIMAGE_ADDON"] != "1" and
                    seed == "ship-addon"):
                yield "FORCE-CD-BREAK"
            if source:
                yield "#include <source/%s/%s:%s>" % (series, project, seed)
            else:
                yield "#include <%s/%s/%s>" % (project, series, seed)
            found = True

        if not found:
            raise NoMasterSeeds("No seeds found for master task!")

    def tasks_output_dir(self, project):
        return os.path.join(
            self.config.root, "scratch", project, self.config.full_series,
            self.config.image_type, "tasks")

    def task_packages(self, arch, seed, seedsource):
        """Like seed_packages, but with various special-case hacks."""
        installer_seeds = set(self.list_seeds("installer"))

        for package in self.seed_packages(arch, seedsource):
            # Hackily exclude kernel-image-* from the installer and casper
            # tasks.  Those udebs only exist to satisfy dependencies when
            # building the debian-installer package.
            if seed in installer_seeds and package.startswith("kernel-image-"):
                continue

            # Force the use of live-installer rather than bootstrap-base on
            # squashfs-base images.  Seed expansion doesn't do the right
            # thing here because the installer seed is expanded before
            # considering server-ship.
            if self.config["CDIMAGE_SQUASHFS_BASE"]:
                if package == "bootstrap-base":
                    package = "live-installer"

            # For precise, some flavours use a different kernel on i386.
            # germinate doesn't currently support this without duplicating
            # the entire boot and installer seeds, so we hack them instead.
            if (self.config.project in ("xubuntu", "lubuntu") and
                    self.config.series == "precise" and arch == "i386"):
                if seed in installer_seeds:
                    package = package.replace("-generic-pae-di", "-generic-di")
                if seed == "boot":
                    package = package.replace("-generic-pae", "-generic")

            yield package

    def installer_initrds(self, cpuarch):
        if cpuarch in ("amd64", "i386"):
            return ["cdrom/initrd.gz", "netboot/netboot.tar.gz"]
        elif cpuarch == "hppa":
            return ["cdrom/2.6/initrd.gz", "netboot/2.6/boot.img"]
        elif cpuarch == "ia64":
            return ["cdrom/boot.img", "netboot/netboot.tar.gz"]
        elif cpuarch in ("powerpc", "ppc64el"):
            return ["cdrom/initrd.gz", "netboot/initrd.gz"]
        elif cpuarch == "sparc":
            return ["cdrom/initrd.gz", "netboot/initrd.gz"]
        else:
            return []

    def installer_subarches(self, cpuarch):
        if cpuarch == "powerpc":
            return ["powerpc", "powerpc64"]
        else:
            return ["."]

    def initrd_packages(self, initrd, arch):
        manifest_path = os.path.join(
            find_mirror(self.config, arch), "dists", self.config.series,
            "main", "installer-%s" % arch, "current", "images",
            "MANIFEST.udebs")
        if not os.path.exists(manifest_path):
            return set()
        if initrd.startswith("./"):
            initrd = initrd[2:]
        packages = set()
        with open(manifest_path) as manifest:
            found_initrd = False
            for line in manifest:
                line = line.rstrip("\n")
                if line == initrd:
                    found_initrd = True
                elif found_initrd:
                    if line and not line[0].isspace():
                        break
                    else:
                        packages.add(line.split()[0])
        return packages

    def common_initrd_packages(self, arch):
        initrd_packages_sets = []
        # Remove installer packages that are in both the cdrom and
        # netboot initrds; there's no point duplicating these.
        cpuarch = arch.split("+")[0]
        initrds = self.installer_initrds(cpuarch)
        subarches = self.installer_subarches(cpuarch)
        for initrd in initrds:
            for subarch in subarches:
                initrd_packages_sets.append(self.initrd_packages(
                    "%s/%s" % (subarch, initrd), cpuarch))
        if initrd_packages_sets:
            return set.intersection(*initrd_packages_sets)
        else:
            return set()

    def task_project(self, project):
        # ubuntu-server really wants ubuntu-* tasks.
        if project == "ubuntu-server":
            return "ubuntu"
        else:
            return project

    def task_headers(self, arch, seed):
        headers = {}
        try:
            with open("%s.seedtext" % self.seed_path(arch, seed)) as seedtext:
                for line in seedtext:
                    if not line.lower().startswith("task-"):
                        continue
                    line = line.rstrip("\n")
                    key, value = line.split(":", 1)
                    key = key[5:].lower()
                    value = value.lstrip()
                    headers[key] = value
        except IOError as e:
            if e.errno != errno.ENOENT:
                raise
        return headers

    def seed_task_mapping(self, project, arch):
        task_project = self.task_project(project)
        for seed in self.list_seeds("all"):
            # Tasks implemented via tasksel, with Task-Seeds to indicate
            # task/seed mapping.
            task = seed
            headers = self.task_headers(arch, seed)
            if not headers:
                continue
            input_seeds = [seed] + headers.get("seeds", "").split()
            if "per-derivative" in headers:
                # Edubuntu is odd; it's structured as an add-on to
                # Ubuntu, so sometimes we need to create ubuntu-* tasks.
                # At the moment I don't see a better approach than
                # hardcoding the task names.
                if project == "edubuntu" and task in ("desktop", "live"):
                    task = "ubuntu-%s" % task
                else:
                    task = "%s-%s" % (task_project, task)

            yield input_seeds, task

    def write_tasks_project(self, project, source=False):
        if source:
            master_project = "source"
        else:
            master_project = project
        output_dir = self.tasks_output_dir(master_project)
        osextras.ensuredir(output_dir)

        for arch in self.config.arches:
            initrd_packages = self.common_initrd_packages(arch)
            packages = defaultdict(list)
            cpparch = arch.replace("+", "_")
            for seed in self.list_seeds("all"):
                if seed == "supported":
                    seedsource = "%s+build-depends" % seed
                else:
                    seedsource = seed
                seed_path = self.seed_path(arch, seedsource)
                if not os.path.exists(seed_path):
                    continue
                with open(os.path.join(output_dir, seed), "a") as task_file:
                    print("#ifdef ARCH_%s" % cpparch, file=task_file)
                    for package in sorted(
                            self.task_packages(arch, seed, seedsource)):
                        if package not in initrd_packages:
                            packages[seed].append(package)
                            print(package, file=task_file)
                    print("#endif /* ARCH_%s */" % cpparch, file=task_file)

            tasks = defaultdict(list)
            for input_seeds, task in self.seed_task_mapping(project, arch):
                for input_seed in input_seeds:
                    for pkg in packages.get(input_seed, []):
                        tasks[pkg].append(task)

            # Help debian-cd to regenerate Task headers, to make sure that
            # we don't accidentally end up out of sync with the archive and
            # break the package installation step.
            # Note that the results of this will be wrong for source images,
            # but that doesn't matter since they won't be used there.
            override_path = os.path.join(output_dir, "override.%s" % arch)
            with open(override_path, "w") as override:
                for pkg, tasknames in sorted(tasks.items()):
                    print(
                        "%s  Task  %s" % (pkg, ", ".join(tasknames)),
                        file=override)
            # Help debian-cd to get priorities in sync with the current base
            # system, so that debootstrap >= 0.3.1 can work out the correct
            # set of packages to install.
            important_path = os.path.join(output_dir, "important.%s" % arch)
            with open(important_path, "w") as important_file:
                important = []
                for seed in self.list_seeds("debootstrap"):
                    important.extend(packages.get(seed, []))
                for pkg in sorted(important):
                    if not re_not_base.match(pkg):
                        print(pkg, file=important_file)

            with open(os.path.join(output_dir, "MASTER"), "w") as master:
                for entry in self.master_task_entries(project, source=source):
                    print(entry, file=master)

    def write_tasks(self):
        if self.config.image_type == "source":
            output_dir = self.tasks_output_dir("source")
            osextras.mkemptydir(output_dir)

            # Generate task output for all source projects.
            for project in self.config.all_projects:
                # TODO cjwatson 2013-04-25: Layering violation; refactor.
                project_output = GerminateOutput(
                    self.config,
                    os.path.join(
                        self.config.root, "scratch", project,
                        self.config.full_series, self.config.image_type,
                        "germinate"))
                project_output.write_tasks_project(project, source=True)
                # TODO: write_tasks_project should just write these files
                # with the names we need in the first place.
                for entry in os.listdir(output_dir):
                    if ":" not in entry:
                        os.rename(
                            os.path.join(output_dir, entry),
                            os.path.join(
                                output_dir, "%s:%s" % (project, entry)))

            # Make a super-master task file.
            with open(os.path.join(output_dir, "MASTER"), "w") as master:
                for project in self.config.all_projects:
                    print(
                        "#include <source/%s/%s:MASTER>" % (
                            self.config.series, project),
                        file=master)
        else:
            osextras.mkemptydir(self.tasks_output_dir(self.config.project))
            self.write_tasks_project(self.config.project)

    def diff_tasks(self, output=None):
        tasks_dir = self.tasks_output_dir(self.config.project)
        previous_tasks_dir = "%s-previous" % tasks_dir
        for seed in ["MASTER"] + list(self.list_seeds("all")):
            old = os.path.join(previous_tasks_dir, seed)
            new = os.path.join(tasks_dir, seed)
            if os.path.exists(old) and os.path.exists(new):
                kwargs = {}
                if output is not None:
                    kwargs["stdout"] = output
                subprocess.call(["diff", "-u", old, new], **kwargs)

    def update_tasks(self, date):
        tasks_dir = self.tasks_output_dir(self.config.project)
        previous_tasks_dir = "%s-previous" % tasks_dir
        debian_cd_tasks_dir = os.path.join(
            self.config.root, "debian-cd", "tasks", "auto",
            self.config.image_type, self.config.project,
            self.config.full_series)

        task_recipients = []
        task_mail_path = os.path.join(self.config.root, "etc", "task-mail")
        if os.path.exists(task_mail_path):
            with open(task_mail_path) as task_mail:
                task_recipients = task_mail.read().split()
        if task_recipients:
            read, write = os.pipe()
            pid = os.fork()
            if pid == 0:  # child
                try:
                    os.close(read)
                    with os.fdopen(write, "w", 1) as write_file:
                        self.diff_tasks(output=write_file)
                    os._exit(0)
                except Exception:
                    traceback.print_exc()
                finally:
                    os._exit(1)
            else:  # parent
                os.close(write)
                with os.fdopen(read) as read_file:
                    send_mail(
                        "Task changes for %s %s/%s on %s" % (
                            self.config.capproject, self.config.image_type,
                            self.config.full_series, date),
                        "update-tasks", task_recipients, read_file)
                os.waitpid(pid, 0)

        self.diff_tasks()

        osextras.mkemptydir(debian_cd_tasks_dir)
        osextras.mkemptydir(previous_tasks_dir)
        for entry in os.listdir(tasks_dir):
            shutil.copy2(
                os.path.join(tasks_dir, entry),
                os.path.join(debian_cd_tasks_dir, entry))
            shutil.copy2(
                os.path.join(tasks_dir, entry),
                os.path.join(previous_tasks_dir, entry))
