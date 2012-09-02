# -*- coding: utf-8 -*-
"""

    @author: Fabio Erculiani <lxnay@sabayon.org>
    @contact: lxnay@sabayon.org
    @copyright: Fabio Erculiani
    @license: GPL-2

    B{Entropy Command Line Client}.

"""
import os
import sys
import argparse
import copy

from entropy.i18n import _
from entropy.output import darkred, red, brown, purple, teal, blue, \
    darkgreen, bold
from entropy.const import etpConst

from solo.commands.descriptor import SoloCommandDescriptor
from solo.commands.command import SoloCommand
from solo.utils import print_table

class SoloRepo(SoloCommand):
    """
    Main Solo Repo command.
    """

    NAME = "repo"
    ALIASES = []
    ALLOW_UNPRIVILEGED = False

    INTRODUCTION = """\
Manage Entropy Repositories.
"""
    SEE_ALSO = ""

    def __init__(self, args):
        SoloCommand.__init__(self, args)
        self._nsargs = None
        self._commands = []

    def man(self):
        """
        Overridden from SoloCommand.
        """
        return self._man()

    def _get_parser(self):
        """
        Overridden from SoloCommand.
        """
        self._real_command = sys.argv[0]
        _commands = []

        descriptor = SoloCommandDescriptor.obtain_descriptor(
            SoloRepo.NAME)
        parser = argparse.ArgumentParser(
            description=descriptor.get_description(),
            formatter_class=argparse.RawDescriptionHelpFormatter,
            prog="%s %s" % (sys.argv[0], SoloRepo.NAME))

        subparsers = parser.add_subparsers(
            title="action", description=_("manage repositories"),
            help=_("available commands"))

        enable_parser = subparsers.add_parser("enable",
            help=_("enable repositories"))
        enable_parser.add_argument("repo", nargs='+',
                                   metavar="<repo>",
                                   help=_("repository name"))
        enable_parser.set_defaults(func=self._enable)
        _commands.append("enable")

        disable_parser = subparsers.add_parser("disable",
            help=_("disable repositories"))
        disable_parser.add_argument("repo", nargs='+',
                                    metavar="<repo>",
                                    help=_("repository name"))
        disable_parser.set_defaults(func=self._disable)
        _commands.append("disable")

        add_parser = subparsers.add_parser("add",
            help=_("add a repository"))
        add_parser.add_argument("string", nargs='+',
                                metavar="<string>",
                                help=_("repository string"))
        add_parser.set_defaults(func=self._add)
        _commands.append("add")

        remove_parser = subparsers.add_parser("remove",
            help=_("remove a repository"))
        remove_parser.add_argument("repo", nargs='+',
                                   metavar="<repo>",
                                   help=_("repository name"))
        remove_parser.set_defaults(func=self._remove)
        _commands.append("remove")

        list_parser = subparsers.add_parser("list",
            help=_("list active repositories"))
        list_parser.set_defaults(func=self._list)
        _commands.append("list")

        mirrorsort_parser = subparsers.add_parser("mirrorsort",
            help=_("reorder mirrors basing on response time"))
        mirrorsort_parser.add_argument("repo", nargs='+',
                                       metavar="<repo>",
                                       help=_("repository name"))
        mirrorsort_parser.add_argument(
            "--simulate", action="store_true",
            default=False, help=_("simulate execution"))
        mirrorsort_parser.set_defaults(func=self._mirrorsort)
        _commands.append("mirrorsort")

        merge_parser = subparsers.add_parser("merge",
            help=_("merge content of source repository to destination"))
        merge_parser.add_argument("source", metavar="<source>",
                                  help=_("source repository"))
        merge_parser.add_argument("dest", metavar="<destination>",
                                  help=_("destination repository"))
        merge_parser.add_argument(
            "--conflicts", action="store_true",
            default=False,
            help=_("also remove dependency conflicts during merge"))
        merge_parser.set_defaults(func=self._merge)
        _commands.append("merge")

        self._commands = _commands
        return parser

    def parse(self):
        """
        Parse command
        """
        parser = self._get_parser()
        try:
            nsargs = parser.parse_args(self._args)
        except IOError as err:
            sys.stderr.write("%s\n" % (err,))
            return parser.print_help, []

        self._nsargs = nsargs
        return self._call_locked, [nsargs.func]

    def bashcomp(self, last_arg):
        """
        Overridden from SoloCommand.
        """
        outcome = []
        parser = self._get_parser()
        try:
            command = self._args[0]
        except IndexError:
            command = None

        if not self._args:
            # show all the commands
            outcome += self._commands

        elif command not in self._commands:
            # return all the commands anyway
            # last_arg will filter them
            outcome += self._commands

        elif command == "enable":
            settings = self._entropy_bashcomp().Settings()
            excluded_repos = list(settings['repositories']['excluded'])
            outcome += excluded_repos

        elif command == "disable":
            settings = self._entropy_bashcomp().Settings()
            avail_repos = list(settings['repositories']['available'])
            outcome += avail_repos

        elif command == "add":
            # complete with "repository|" ?
            outcome.append("repository=")

        elif command == "remove":
            settings = self._entropy_bashcomp().Settings()
            avail_repos = list(settings['repositories']['available'])
            excluded_repos = list(settings['repositories']['excluded'])
            outcome += excluded_repos
            outcome += avail_repos

        elif command == "list":
            # nothing to do
            pass

        elif command == "mirrorsort":
            settings = self._entropy_bashcomp().Settings()
            avail_repos = list(settings['repositories']['available'])
            outcome += avail_repos

        elif command == "merge":
            settings = self._entropy_bashcomp().Settings()
            avail_repos = list(settings['repositories']['available'])
            outcome += avail_repos

        return self._bashcomp(sys.stdout, last_arg, outcome)

    def _enable(self, entropy_client):
        """
        Solo Repo Enable command.
        """
        exit_st = 0
        settings = entropy_client.Settings()
        excluded_repos = settings['repositories']['excluded']
        available_repos = settings['repositories']['available']

        for repo in self._nsargs.repo:

            if repo in available_repos:
                entropy_client.output(
                    "[%s] %s" % (
                        purple(repo),
                        blue(_("repository already enabled")),),
                    level="warning", importance=1)
                exit_st = 1
                continue

            if repo not in excluded_repos:
                entropy_client.output(
                    "[%s] %s" % (
                        purple(repo),
                        blue(_("repository not available")),),
                    level="warning", importance=1)
                exit_st = 1
                continue

            _exit_st = self._enable_repo(entropy_client, repo)
            if _exit_st != 0:
                exit_st = _exit_st

        return exit_st

    def _enable_repo(self, entropy_client, repo):
        """
        Solo Repo Enable for given repository.
        """
        enabled = entropy_client.enable_repository(repo)
        if enabled:
            entropy_client.output(
                "[%s] %s" % (
                    teal(repo),
                    blue(_("repository enabled")),))
            return 0

        entropy_client.output(
            "[%s] %s" % (
                purple(repo),
                blue(_("cannot enable repository")),),
            level="warning", importance=1)
        return 1

    def _disable(self, entropy_client):
        """
        Solo Repo Disable command.
        """
        exit_st = 0
        settings = entropy_client.Settings()
        excluded_repos = settings['repositories']['excluded']
        available_repos = settings['repositories']['available']

        for repo in self._nsargs.repo:

            if repo in excluded_repos:
                entropy_client.output(
                    "[%s] %s" % (
                        purple(repo),
                        blue(_("repository already disabled")),),
                    level="warning", importance=1)
                exit_st = 1
                continue

            if repo not in available_repos:
                entropy_client.output(
                    "[%s] %s" % (
                        purple(repo),
                        blue(_("repository not available")),),
                    level="warning", importance=1)
                exit_st = 1
                continue

            _exit_st = self._disable_repo(entropy_client, repo)
            if _exit_st != 0:
                exit_st = _exit_st

        return exit_st

    def _disable_repo(self, entropy_client, repo):
        """
        Solo Repo Disable for given repository.
        """
        disabled = False
        try:
            disabled = entropy_client.disable_repository(repo)
        except ValueError:
            entropy_client.output(
                "[%s] %s" % (
                    purple(repo),
                    blue(_("cannot disable repository")),),
                level="warning", importance=1)
            return 1

        if disabled:
            entropy_client.output(
                "[%s] %s" % (
                    teal(repo),
                    blue(_("repository disabled")),))
            return 0

        entropy_client.output(
            "[%s] %s" % (
                purple(repo),
                blue(_("cannot disable repository")),),
            level="warning", importance=1)
        return 1

    def _add(self, entropy_client):
        """
        Solo Repo Add command.
        """
        exit_st = 0

        for string in self._nsargs.string:

            if not string:
                entropy_client.output(
                    "[%s] %s" % (
                        purple(string),
                        blue(_("invalid data, skipping")),),
                    level="warning", importance=1)
                exit_st = 1
                continue

            _exit_st = self._add_repo(entropy_client, string)
            if _exit_st != 0:
                exit_st = _exit_st

        return exit_st

    def _add_repo(self, entropy_client, repo_string):
        """
        Solo Repo Add for given repository.
        """
        settings = entropy_client.Settings()
        current_branch = settings['repositories']['branch']
        current_product = settings['repositories']['product']
        available_repos = settings['repositories']['available']

        entropy_client.output(
            "%s: %s" % (
                teal(_("Adding repository string")),
                blue(repo_string),))

        try:
            repoid, repodata = settings._analyze_client_repo_string(
                repo_string, current_branch, current_product)
        except AttributeError as err:
            entropy_client.output(
                "[%s] %s: %s" % (
                    purple(repo_string),
                    blue(_("invalid repository string")),
                    err,),
                level="warning", importance=1
            )
            return 1

        toc = []
        toc.append((
                purple(_("Repository id:")),
                teal(repoid)))
        toc.append((
                darkgreen(_("Description:")),
                teal(repodata['description'])))
        toc.append((
                purple(_("Repository format:")),
                darkgreen(repodata['dbcformat'])))

        for pkg_url in repodata['plain_packages']:
            toc.append((purple(_("Packages URL:")), pkg_url))
        db_url = repodata['plain_database']
        if not db_url:
            db_url = _("None given")

        toc.append((purple(_("Repository URL:")), darkgreen(db_url)))
        toc.append(" ")
        print_table(toc)

        added = entropy_client.add_repository(repodata)
        if added:
            entropy_client.output(
                "[%s] %s" % (
                    purple(repoid),
                    blue(_("repository added succesfully")),))
        else:
            entropy_client.output(
                "[%s] %s" % (
                    purple(repoid),
                    blue(_("cannot add repository")),),
                level="warning", importance=1)

        return 0

    def _remove(self, entropy_client):
        """
        Solo Repo Remove command.
        """
        exit_st = 0
        settings = entropy_client.Settings()
        excluded_repos = settings['repositories']['excluded']
        available_repos = settings['repositories']['available']
        repos = set(list(excluded_repos.keys()) + \
                        list(available_repos.keys()))

        for repo in self._nsargs.repo:

            if repo not in repos:
                entropy_client.output(
                    "[%s] %s" % (
                        purple(repo),
                        blue(_("repository id not available")),),
                    level="warning", importance=1)
                exit_st = 1
                continue

            _exit_st = self._remove_repo(entropy_client, repo)
            if _exit_st != 0:
                exit_st = _exit_st

        return exit_st

    def _remove_repo(self, entropy_client, repo):
        """
        Solo Repo Remove for given repository.
        """
        removed = entropy_client.remove_repository(repo)
        if removed:
            entropy_client.output(
                "[%s] %s" % (
                    purple(repo),
                    blue(_("repository removed succesfully")),))
            return 0

        entropy_client.output(
            "[%s] %s" % (
                purple(repo),
                blue(_("cannot remove repository")),),
            level="warning", importance=1)
        return 1

    def _list(self, entropy_client):
        """
        Solo Repo List command.
        """
        settings = entropy_client.Settings()
        excluded_repos = settings['repositories']['excluded']
        available_repos = settings['repositories']['available']
        default_repo = settings['repositories']['default_repository']
        repositories = entropy_client.repositories()

        for repository in repositories:
            repo_data = available_repos.get(repository)
            desc = _("N/A")
            if repo_data is None:
                repo_data = excluded_repos.get(repository)
            if repo_data is not None:
                desc = repo_data.get('description', desc)

            repo_str = "  "
            if repository == default_repo:
                repo_str = purple("* ")
            entropy_client.output(
                "%s%s\n    %s" % (
                    repo_str, darkgreen(repository),
                    brown(desc),),
                level="generic")

        return 0

    def _mirrorsort(self, entropy_client):
        """
        Solo Repo Mirrorsort command.
        """
        exit_st = 0
        settings = entropy_client.Settings()
        excluded_repos = settings['repositories']['excluded']
        available_repos = settings['repositories']['available']
        simulate = self._nsargs.simulate

        for repo in self._nsargs.repo:

            try:
                repo_data = entropy_client.reorder_mirrors(
                    repo, dry_run = simulate)
            except KeyError:
                entropy_client.output(
                    "[%s] %s" % (
                        purple(repo),
                        blue(_("repository not available")),),
                    level="warning", importance=1)
                exit_st = 1
                continue

            # show new order, this doesn't take into account
            # fallback mirrors which are put at the end of
            # the list by SystemSettings logic.
            mirrors = copy.copy(repo_data['plain_packages'])
            if mirrors and not simulate:
                mirrors.reverse()
                entropy_client.output(
                    "[%s] %s" % (
                        teal(repo),
                        darkgreen(_("mirror order:")),))
                count = 0
                for mirror in mirrors:
                    count += 1
                    entropy_client.output(
                        "  %d. %s" % (count, brown(mirror),))

            entropy_client.output(
                "[%s] %s" % (
                    teal(repo),
                    blue(_("mirrors sorted successfully")),))

        return exit_st

    def _merge(self, entropy_client):
        """
        Solo Repo Merge command.
        """
        settings = entropy_client.Settings()
        source = self._nsargs.source
        dest = self._nsargs.dest
        remove_conflicts = self._nsargs.conflicts

        # validate source repo
        available_repos = settings['repositories']['available']
        if source not in available_repos:
            entropy_client.output(
                "[%s] %s" % (
                    purple(source),
                    blue(_("repository not found")),),
                level="error", importance=1)
            return 2
        if dest not in available_repos:
            entropy_client.output(
                "[%s] %s" % (
                    purple(dest),
                    blue(_("repository not found")),),
                level="error", importance=1)
            return 2

        # source = dest?
        if source == dest:
            entropy_client.output(
                "[%s] %s" % (
                    purple(dest),
                    blue(_("repository cannot be source "
                          "and destination")),),
                level="error", importance=1)
            return 3

        entropy_client.output(
            "[%s] %s" % (
                teal(source) + "=>" + purple(dest),
                blue(_("merging repositories")),))

        repo_meta = settings['repositories']['available'][dest]
        repo_path = os.path.join(
            repo_meta['dbpath'],
            etpConst['etpdatabasefile'])

        # make sure all the repos are closed
        entropy_client.close_repositories()
        # this way it's open read/write
        dest_db = entropy_client.open_generic_repository(
            repo_path)

        entropy_client.output(
            "[%s] %s" % (
                teal(source),
                blue(_("working on repository")),))
        source_db = entropy_client.open_repository(source)

        pkg_ids = source_db.listAllPackageIds(order_by = 'atom')
        total = len(pkg_ids)
        count = 0
        conflict_cache = set()
        for pkg_id in pkg_ids:
            count += 1
            pkg_meta = source_db.getPackageData(
                pkg_id, get_content = True,
                content_insert_formatted = True)

            entropy_client.output(
                "[%s:%s|%s] %s" % (
                    purple(str(count)),
                    darkgreen(str(total)),
                    teal(pkg_meta['atom']), blue(_("merging package")),),
                back = True)

            target_pkg_ids = dest_db.getPackagesToRemove(
                pkg_meta['name'], pkg_meta['category'],
                pkg_meta['slot'], pkg_meta['injected'])
            if remove_conflicts:
                for conflict in pkg_meta['conflicts']:
                    if conflict in conflict_cache:
                        continue
                    conflict_cache.add(conflict)
                    matches, rc = dest_db.atomMatch(conflict,
                        multiMatch = True)
                    target_pkg_ids |= matches

            for target_pkg_id in target_pkg_ids:
                dest_db.removePackage(target_pkg_id)
            dest_pkg_id = dest_db.addPackage(pkg_meta,
                formatted_content = True)
            dest_db.commit()

        entropy_client.output(
            "[%s] %s" % (
                teal(source),
                blue(_("done merging packages")),))

        dest_db.commit()
        dest_db.close()
        # close all repos again
        entropy_client.close_repositories()
        return 0


SoloCommandDescriptor.register(
    SoloCommandDescriptor(
        SoloRepo,
        SoloRepo.NAME,
        _("manage repositories"))
    )
