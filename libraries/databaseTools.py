#!/usr/bin/python
'''
    # DESCRIPTION:
    # Entropy Database Interface

    Copyright (C) 2007-2008 Fabio Erculiani

    This program is free software; you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation; either version 2 of the License, or
    (at your option) any later version.

    This program is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with this program; if not, write to the Free Software
    Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA
'''

from entropyConstants import *
from outputTools import *
import exceptionTools
Text = TextInterface()
try: # try with sqlite3 from python 2.5 - default one
    from sqlite3 import dbapi2
except ImportError: # fallback to embedded pysqlite
    try:
        from pysqlite2 import dbapi2
    except ImportError, e:
        raise exceptionTools.SystemError("Entropy needs a working sqlite+pysqlite or Python compiled with sqlite support. Error: %s" % (str(e),))
import dumpTools

class etpDatabase:

    import entropyTools
    def __init__(self, readOnly = False, noUpload = False, dbFile = None, clientDatabase = False, xcache = False, dbname = etpConst['serverdbid'], indexing = True, OutputInterface = Text, ServiceInterface = None):

        if dbFile == None:
            raise exceptionTools.IncorrectParameter("IncorrectParameter: valid database path needed")

        # setup output interface
        self.OutputInterface = OutputInterface
        self.updateProgress = self.OutputInterface.updateProgress
        self.askQuestion = self.OutputInterface.askQuestion
        # setup service interface
        self.ServiceInterface = ServiceInterface
        self.readOnly = readOnly
        self.noUpload = noUpload
        self.clientDatabase = clientDatabase
        self.xcache = xcache
        self.dbname = dbname
        self.indexing = indexing
        if not self.entropyTools.is_user_in_entropy_group():
            # forcing since we won't have write access to db
            self.indexing = False
        # live systems don't like wasting RAM
        if self.entropyTools.islive():
            self.indexing = False
        self.dbFile = dbFile
        self.dbclosed = False
        self.server_repo = None

        if not self.clientDatabase:
            self.server_repo = self.dbname[len(etpConst['serverdbid']):]
            self.create_dbstatus_data()

        # no caching for non root and server connections
        if (self.dbname.startswith(etpConst['serverdbid'])) or (not self.entropyTools.is_user_in_entropy_group()):
            self.xcache = False
        self.live_cache = {}

        # create connection
        self.connection = dbapi2.connect(dbFile,timeout=300.0)
        self.cursor = self.connection.cursor()

        if not self.clientDatabase and not self.readOnly:
            # server side is calling
            # lock mirror remotely and ensure to have latest database revision
            self.doServerDatabaseSyncLock(self.noUpload)

        if os.access(self.dbFile,os.W_OK) and self.doesTableExist('baseinfo') and self.doesTableExist('extrainfo'):
            if self.entropyTools.islive():
                # check where's the file
                if etpConst['systemroot']:
                    self.databaseStructureUpdates()
            else:
                self.databaseStructureUpdates()

    def __del__(self):
        if not self.dbclosed:
            self.closeDB()

    def create_dbstatus_data(self):
        taint_file = self.ServiceInterface.get_local_database_taint_file(self.server_repo)
        if not etpDbStatus.has_key(self.dbFile):
            etpDbStatus[self.dbFile] = {}
            etpDbStatus[self.dbFile]['tainted'] = False
            etpDbStatus[self.dbFile]['bumped'] = False
        if os.path.isfile(taint_file):
            etpDbStatus[self.dbFile]['tainted'] = True
            etpDbStatus[self.dbFile]['bumped'] = True

    def doServerDatabaseSyncLock(self, noUpload):

        # check if the database is locked locally
        # self.server_repo
        lock_file = self.ServiceInterface.MirrorsService.get_database_lockfile(self.server_repo)
        if os.path.isfile(lock_file):
            self.updateProgress(
                                    red("Entropy database is already locked by you :-)"),
                                    importance = 1,
                                    type = "info",
                                    header = red(" * ")
                                )
        else:
            # check if the database is locked REMOTELY
            self.updateProgress(
                                    red("Locking and Syncing Entropy database..."),
                                    importance = 1,
                                    type = "info",
                                    header = red(" * "),
                                    back = True
                                )
            for uri in self.ServiceInterface.get_remote_mirrors(self.server_repo):
                given_up = self.ServiceInterface.MirrorsService.mirror_lock_check(uri, repo = self.server_repo)
                if given_up:
                    crippled_uri = self.entropyTools.extractFTPHostFromUri(uri)
                    self.updateProgress(
                                            darkgreen("Mirrors status table:"),
                                            importance = 1,
                                            type = "info",
                                            header = brown(" * ")
                                        )
                    dbstatus = self.ServiceInterface.MirrorsService.get_mirrors_lock(repo = self.server_repo)
                    for db in dbstatus:
                        db[1] = green("Unlocked")
                        if (db[1]):
                            db[1] = red("Locked")
                        db[2] = green("Unlocked")
                        if (db[2]):
                            db[2] = red("Locked")

                        crippled_uri = self.entropyTools.extractFTPHostFromUri(db[0])
                        self.updateProgress(
                                                bold("%s: ")+red("[")+brown("DATABASE: %s")+red("] [")+brown("DOWNLOAD: %s")+red("]") % (crippled_uri,db[1],db[2],),
                                                importance = 1,
                                                type = "info",
                                                header = "\t"
                                            )

                    raise exceptionTools.OnlineMirrorError("OnlineMirrorError: cannot lock mirror %s" % (crippled_uri,))

            # if we arrive here, it is because all the mirrors are unlocked
            self.ServiceInterface.MirrorsService.lock_mirrors(True, repo = self.server_repo)
            self.ServiceInterface.MirrorsService.sync_databases(noUpload, repo = self.server_repo)

    def closeDB(self):

        self.dbclosed = True

        # if the class is opened readOnly, close and forget
        if self.readOnly:
            self.cursor.close()
            self.connection.close()
            return

        if self.clientDatabase:
            self.commitChanges()
            self.cursor.close()
            self.connection.close()
            return

        if not etpDbStatus[self.dbFile]['tainted']:
            # we can unlock it, no changes were made
            self.ServiceInterface.MirrorsService.lock_mirrors(False, repo = self.server_repo)
        else:
            self.updateProgress(
                                    darkgreen("Mirrors have not been unlocked. Run activator."),
                                    importance = 1,
                                    type = "info",
                                    header = brown(" * ")
                                )

        self.commitChanges()
        #self.vacuum()
        self.cursor.close()
        self.connection.close()

    def vacuum(self):
        self.cursor.execute("vacuum")

    def commitChanges(self):

        if self.readOnly:
            return

        try:
            self.connection.commit()
        except:
            pass

        if not self.clientDatabase:
            self.taintDatabase()
            if (etpDbStatus[self.dbFile]['tainted']) and \
                (not etpDbStatus[self.dbFile]['bumped']):
                    # bump revision, setting DatabaseBump causes the session to just bump once
                    etpDbStatus[self.dbFile]['bumped'] = True
                    self.revisionBump()

    def taintDatabase(self):
        # if it's equo to open it, this should be avoided
        if self.clientDatabase:
            return
        # taint the database status
        taint_file = self.ServiceInterface.get_local_database_taint_file(repo = self.server_repo)
        f = open(taint_file,"w")
        f.write(etpConst['currentarch']+" database tainted\n")
        f.flush()
        f.close()
        etpDbStatus[self.dbFile]['tainted'] = True

    def untaintDatabase(self):
        if (self.clientDatabase): # if it's equo to open it, this should be avoided
            return
        etpDbStatus[self.dbFile]['tainted'] = False
        # untaint the database status
        taint_file = self.ServiceInterface.get_local_database_taint_file(repo = self.server_repo)
        if os.path.isfile(taint_file):
            os.remove(taint_file)

    def revisionBump(self):
        revision_file = self.ServiceInterface.get_local_database_revision_file(repo = self.server_repo)
        if not os.path.isfile(revision_file):
            revision = 0
        else:
            f = open(revision_file,"r")
            revision = int(f.readline().strip())
            revision += 1
            f.close()
        f = open(revision_file,"w")
        f.write(str(revision)+"\n")
        f.flush()
        f.close()

    def isDatabaseTainted(self):
        taint_file = self.ServiceInterface.get_local_database_taint_file(repo = self.server_repo)
        if os.path.isfile(taint_file):
            return True
        return False

    # never use this unless you know what you're doing
    def initializeDatabase(self):
        self.checkReadOnly()
        self.cursor.executescript(etpConst['sql_destroy'])
        self.cursor.executescript(etpConst['sql_init'])
        self.databaseStructureUpdates()
        self.commitChanges()

    def checkReadOnly(self):
        if (self.readOnly):
            raise exceptionTools.OperationNotPermitted("OperationNotPermitted: can't do that on a readonly database.")

    # check for /usr/portage/profiles/updates changes
    def serverUpdatePackagesData(self):

        etpConst['server_treeupdatescalled'].add(self.server_repo)

        repo_updates_file = self.ServiceInterface.get_local_database_treeupdates_file(self.server_repo)
        doRescan = False

        stored_digest = self.retrieveRepositoryUpdatesDigest(self.server_repo)
        if stored_digest == -1:
            doRescan = True

        # check portage files for changes if doRescan is still false
        portage_dirs_digest = "0"
        if not doRescan:

            if repositoryUpdatesDigestCache_disk.has_key(self.server_repo):
                portage_dirs_digest = repositoryUpdatesDigestCache_disk.get(self.server_repo)
            else:
                from entropy import SpmInterface
                SpmIntf = SpmInterface(self.OutputInterface)
                Spm = SpmIntf.intf
                # grab portdir
                updates_dir = etpConst['systemroot']+Spm.get_spm_setting("PORTDIR")+"/profiles/updates"
                if os.path.isdir(updates_dir):
                    # get checksum
                    mdigest = self.entropyTools.md5sum_directory(updates_dir, get_obj = True)
                    # also checksum etpConst['etpdatabaseupdatefile']
                    if os.path.isfile(repo_updates_file):
                        f = open(repo_updates_file)
                        block = f.read(1024)
                        while block:
                            mdigest.update(block)
                            block = f.read(1024)
                        f.close()
                    portage_dirs_digest = mdigest.hexdigest()
                    repositoryUpdatesDigestCache_disk[self.server_repo] = portage_dirs_digest
                del updates_dir

        if doRescan or (str(stored_digest) != str(portage_dirs_digest)):

            # force parameters
            self.readOnly = False
            self.noUpload = True

            # reset database tables
            self.clearTreeupdatesEntries(self.server_repo)

            from entropy import SpmInterface
            SpmIntf = SpmInterface(self.OutputInterface)
            Spm = SpmIntf.intf
            updates_dir = etpConst['systemroot']+Spm.get_spm_setting("PORTDIR")+"/profiles/updates"
            update_files = self.entropyTools.sortUpdateFiles(os.listdir(updates_dir))
            update_files = [os.path.join(updates_dir,x) for x in update_files]
            # now load actions from files
            update_actions = []
            for update_file in update_files:
                f = open(update_file,"r")
                mycontent = f.readlines()
                f.close()
                lines = [x.strip() for x in mycontent if x.strip()]
                update_actions.extend(lines)

            # add entropy packages.db.repo_updates content
            if os.path.isfile(repo_updates_file):
                f = open(repo_updates_file,"r")
                mycontent = f.readlines()
                f.close()
                lines = [x.strip() for x in mycontent if x.strip() and not x.strip().startswith("#")]
                update_actions.extend(lines)
            # now filter the required actions
            update_actions = self.filterTreeUpdatesActions(update_actions)
            if update_actions:

                self.updateProgress(
                                        bold("ATTENTION: ")+red("forcing package updates. Syncing with %s") % (blue(updates_dir),),
                                        importance = 1,
                                        type = "info",
                                        header = brown(" * ")
                                    )
                # lock database
                self.doServerDatabaseSyncLock(self.noUpload)
                # now run queue
                try:
                    self.runTreeUpdatesActions(update_actions)
                except:
                    # destroy digest
                    self.setRepositoryUpdatesDigest(self.server_repo, "-1")
                    raise

                # store new actions
                self.addRepositoryUpdatesActions(self.server_repo,update_actions)

            # store new digest into database
            self.setRepositoryUpdatesDigest(self.server_repo, portage_dirs_digest)

    # client side, no portage dependency
    # lxnay: it is indeed very similar to serverUpdatePackagesData() but I prefer keeping both separate
    # also, we reuse the same caching dictionaries of the server function
    # repositoryUpdatesDigestCache_disk -> client database cache
    # check for repository packages updates
    # this will read database treeupdates* tables and do
    # changes required if running as root.
    def clientUpdatePackagesData(self, clientDbconn, force = False):

        repository = self.dbname[len(etpConst['dbnamerepoprefix']):]
        etpConst['client_treeupdatescalled'].add(repository)

        doRescan = False
        shell_rescan = os.getenv("ETP_TREEUPDATES_RESCAN")
        if shell_rescan: doRescan = True

        # check database digest
        stored_digest = self.retrieveRepositoryUpdatesDigest(repository)
        if stored_digest == -1:
            doRescan = True

        # check stored value in client database
        client_digest = "0"
        if not doRescan:
            client_digest = clientDbconn.retrieveRepositoryUpdatesDigest(repository)

        if doRescan or (str(stored_digest) != str(client_digest)) or force:

            # reset database tables
            clientDbconn.clearTreeupdatesEntries(repository)

            # load updates
            update_actions = self.retrieveTreeUpdatesActions(repository)
            # now filter the required actions
            update_actions = clientDbconn.filterTreeUpdatesActions(update_actions)

            if update_actions:

                self.updateProgress(
                    bold("ATTENTION: ") + \
                    red("forcing packages metadata update. Updating system database using repository id: %s") % (
                            blue(repository),
                    ),
                    importance = 1,
                    type = "info",
                    header = darkred(" * ")
                    )
                # run stuff
                clientDbconn.runTreeUpdatesActions(update_actions)

            # store new digest into database
            clientDbconn.setRepositoryUpdatesDigest(repository, stored_digest)

            # store new actions
            clientDbconn.addRepositoryUpdatesActions(etpConst['clientdbid'],update_actions)

            # clear client cache
            clientDbconn.clearCache()

    # this functions will filter either data from /usr/portage/profiles/updates/*
    # or repository database returning only the needed actions
    def filterTreeUpdatesActions(self, actions):
        new_actions = []
        for action in actions:
            doaction = action.split()
            if doaction[0] == "slotmove":
                # slot move
                atom = doaction[1]
                from_slot = doaction[2]
                to_slot = doaction[3]
                category = atom.split("/")[0]
                matches = self.atomMatch(atom, multiMatch = True)
                found = False
                if matches[1] == 0:
                    # found atom, check slot and category
                    for idpackage in matches[0]:
                        myslot = str(self.retrieveSlot(idpackage))
                        mycategory = self.retrieveCategory(idpackage)
                        if mycategory == category:
                            if (myslot == from_slot) and (myslot != to_slot) and (action not in new_actions):
                                new_actions.append(action)
                                found = True
                                break
                    if found:
                        continue
                # if we get here it means found == False
                # search into dependencies
                atom_key = self.entropyTools.dep_getkey(atom)
                dep_atoms = self.searchDependency(atom_key, like = True, multi = True, strings = True)
                dep_atoms = [x for x in dep_atoms if x.endswith(":"+from_slot) and self.entropyTools.dep_getkey(x) == atom_key]
                if dep_atoms:
                    new_actions.append(action)
            elif doaction[0] == "move":
                atom = doaction[1] # usually a key
                category = atom.split("/")[0]
                matches = self.atomMatch(atom, multiMatch = True)
                found = False
                if matches[1] == 0:
                    for idpackage in matches[0]:
                        mycategory = self.retrieveCategory(idpackage)
                        if (mycategory == category) and (action not in new_actions):
                            new_actions.append(action)
                            found = True
                            break
                    if found:
                        continue
                # if we get here it means found == False
                # search into dependencies
                atom_key = self.entropyTools.dep_getkey(atom)
                dep_atoms = self.searchDependency(atom_key, like = True, multi = True, strings = True)
                dep_atoms = [x for x in dep_atoms if self.entropyTools.dep_getkey(x) == atom_key]
                if dep_atoms:
                    new_actions.append(action)
        return new_actions

    # this is the place to add extra actions support
    def runTreeUpdatesActions(self, actions):

        # just run fixpackages if gentoo-compat is enabled
        if etpConst['gentoo-compat']:
            self.updateProgress(
                                    bold("GENTOO: ")+red("Running fixpackages, could take a while."),
                                    importance = 1,
                                    type = "warning",
                                    header = darkred(" * ")
                                )
            if self.clientDatabase:
                try:
                    Spm = self.ServiceInterface.Spm()
                    Spm.run_fixpackages()
                except:
                    pass
            else:
                self.ServiceInterface.SpmService.run_fixpackages()

        quickpkg_atoms = set()
        for action in actions:
            command = action.split()
            self.updateProgress(
                                    bold("ENTROPY: ")+red("action: %s") % (blue(action),),
                                    importance = 1,
                                    type = "warning",
                                    header = darkred(" * ")
                                )
            if command[0] == "move":
                quickpkg_atoms |= self.runTreeUpdatesMoveAction(command[1:], quickpkg_atoms)
            elif command[0] == "slotmove":
                quickpkg_atoms |= self.runTreeUpdatesSlotmoveAction(command[1:], quickpkg_atoms)

        if quickpkg_atoms and not self.clientDatabase:
            # quickpkg package and packages owning it as a dependency
            try:
                self.runTreeUpdatesQuickpkgAction(quickpkg_atoms)
            except:
                import traceback
                traceback.print_exc()
                self.updateProgress(
                    bold("WARNING: ")+red("Cannot complete quickpkg for atoms: ")+blue(str(list(quickpkg_atoms)))+red(", do it manually."),
                    importance = 1,
                    type = "warning",
                    header = darkred(" * ")
                )
            self.commitChanges()

        # discard cache
        self.clearCache()


    # -- move action:
    # 1) move package key to the new name: category + name + atom
    # 2) update all the dependencies in dependenciesreference to the new key
    # 3) run fixpackages which will update /var/db/pkg files
    # 4) automatically run quickpkg() to build the new binary and
    #    tainted binaries owning tainted iddependency and taint database
    def runTreeUpdatesMoveAction(self, move_command, quickpkg_queue):

        key_from = move_command[0]
        key_to = move_command[1]
        cat_to = key_to.split("/")[0]
        name_to = key_to.split("/")[1]
        matches = self.atomMatch(key_from, multiMatch = True)
        iddependencies_idpackages = set()

        if matches[1] == 0:

            for idpackage in matches[0]:

                slot = self.retrieveSlot(idpackage)
                old_atom = self.retrieveAtom(idpackage)
                new_atom = old_atom.replace(key_from,key_to)

                ### UPDATE DATABASE
                # update category
                self.setCategory(idpackage, cat_to)
                # update name
                self.setName(idpackage, name_to)
                # update atom
                self.setAtom(idpackage, new_atom)

                # look for packages we need to quickpkg again
                # note: quickpkg_queue is simply ignored if self.clientDatabase
                quickpkg_queue.add(key_to+":"+str(slot))

                if not self.clientDatabase:

                    # check for injection and warn the developer
                    injected = self.isInjected(idpackage)
                    if injected:
                        self.updateProgress(
                            bold("INJECT: ")+red("Package %s has been injected. You need to quickpkg it manually to update embedded database !!! Repository database will be updated anyway.") % (blue(new_atom),),
                            importance = 1,
                            type = "warning",
                            header = darkred(" * ")
                        )

        iddeps = self.searchDependency(key_from, like = True, multi = True)
        for iddep in iddeps:
            # update string
            mydep = self.retrieveDependencyFromIddependency(iddep)
            mydep_key = self.entropyTools.dep_getkey(mydep)
            if mydep_key != key_from: # avoid changing wrong atoms -> dev-python/qscintilla-python would
                continue              # become x11-libs/qscintilla if we don't do this check
            mydep = mydep.replace(key_from,key_to)
            # now update
            # dependstable on server is always re-generated
            self.setDependency(iddep, mydep)
            # we have to repackage also package owning this iddep
            iddependencies_idpackages |= self.searchIdpackageFromIddependency(iddep)

        self.commitChanges()
        quickpkg_queue = list(quickpkg_queue)
        for x in range(len(quickpkg_queue)):
            myatom = quickpkg_queue[x]
            myatom = myatom.replace(key_from,key_to)
            quickpkg_queue[x] = myatom
        quickpkg_queue = set(quickpkg_queue)
        for idpackage_owner in iddependencies_idpackages:
            myatom = self.retrieveAtom(idpackage_owner)
            myatom = myatom.replace(key_from,key_to)
            quickpkg_queue.add(myatom)
        return quickpkg_queue


    # -- slotmove action:
    # 1) move package slot
    # 2) update all the dependencies in dependenciesreference owning same matched atom + slot
    # 3) run fixpackages which will update /var/db/pkg files
    # 4) automatically run quickpkg() to build the new binary and tainted binaries owning tainted iddependency and taint database
    def runTreeUpdatesSlotmoveAction(self, slotmove_command, quickpkg_queue):

        atom = slotmove_command[0]
        atomkey = self.entropyTools.dep_getkey(atom)
        slot_from = slotmove_command[1]
        slot_to = slotmove_command[2]
        matches = self.atomMatch(atom, multiMatch = True)
        iddependencies_idpackages = set()

        if matches[1] == 0:

            for idpackage in matches[0]:

                ### UPDATE DATABASE
                # update slot
                self.setSlot(idpackage, slot_to)

                # look for packages we need to quickpkg again
                # note: quickpkg_queue is simply ignored if self.clientDatabase
                quickpkg_queue.add(atom+":"+str(slot_to))

                if not self.clientDatabase:

                    # check for injection and warn the developer
                    injected = self.isInjected(idpackage)
                    if injected:
                        self.updateProgress(
                            bold("INJECT: ")+red("Package %s has been injected. You need to quickpkg it manually to update embedded database !!! Repository database will be updated anyway.") % (blue(atom),),
                            importance = 1,
                            type = "warning",
                            header = darkred(" * ")
                        )

        iddeps = self.searchDependency(atomkey, like = True, multi = True)
        for iddep in iddeps:
            # update string
            mydep = self.retrieveDependencyFromIddependency(iddep)
            mydep_key = self.entropyTools.dep_getkey(mydep)
            if mydep_key != atomkey:
                continue
            if not mydep.endswith(":"+slot_from): # probably slotted dep
                continue
            mydep = mydep.replace(":"+slot_from,":"+slot_to)
            # now update
            # dependstable on server is always re-generated
            self.setDependency(iddep, mydep)
            # we have to repackage also package owning this iddep
            iddependencies_idpackages |= self.searchIdpackageFromIddependency(iddep)

        self.commitChanges()
        for idpackage_owner in iddependencies_idpackages:
            myatom = self.retrieveAtom(idpackage_owner)
            quickpkg_queue.add(myatom)
        return quickpkg_queue

    def runTreeUpdatesQuickpkgAction(self, atoms):

        branch = etpConst['branch']
        # ask branch question
        rc = self.askQuestion("Would you like to continue with the default branch \"%s\" ?" % (branch,))
        if rc == "No":
            # ask which
            while 1:
                branch = readtext("Type your branch: ")
                if branch not in self.listAllBranches():
                    self.updateProgress(
                            bold("ATTENTION: ")+red("Specified branch %s does not exist.") % (blue(branch),),
                            importance = 1,
                            type = "warning",
                            header = darkred(" * ")
                    )
                    continue
                # ask to confirm
                rc = self.askQuestion("Confirm %s ?" % (branch,))
                if rc == "Yes":
                    break

        self.commitChanges()

        package_paths = set()
        runatoms = set()
        for myatom in atoms:
            mymatch = self.atomMatch(myatom)
            if mymatch[0] == -1:
                continue
            myatom = self.retrieveAtom(mymatch[0])
            runatoms.add(myatom)

        for myatom in runatoms:
            self.updateProgress(
                red("repackaging: ")+blue(myatom),
                importance = 1,
                type = "warning",
                header = blue("  # ")
            )
            mydest = self.ServiceInterface.get_local_store_directory(self.server_repo)
            try:
                mypath = self.ServiceInterface.quickpkg(myatom,mydest)
            except:
                # remove broken bin before raising
                mypath = os.path.join(mydest,os.path.basename(myatom)+etpConst['packagesext'])
                if os.path.isfile(mypath):
                    os.remove(mypath)
                import traceback
                traceback.print_exc()
                self.updateProgress(
                    bold("WARNING: ")+red("Cannot complete quickpkg for atom: ")+blue(myatom)+red(", do it manually."),
                    importance = 1,
                    type = "warning",
                    header = darkred(" * ")
                )
                continue
            package_paths.add(mypath)
        packages_data = [(x,branch,False) for x in package_paths]
        idpackages = self.ServiceInterface.add_packages_to_repository(packages_data, repo = self.server_repo)

        if not idpackages:
            self.updateProgress(
                                    bold("ATTENTION: ")+red("reagent update did not run properly. Please update packages manually"),
                                    importance = 1,
                                    type = "warning",
                                    header = darkred(" * ")
                                )

    # this function manages the submitted package
    # if it does not exist, it fires up addPackage
    # otherwise it fires up updatePackage
    def handlePackage(self, etpData, forcedRevision = -1):

        self.checkReadOnly()

        # build atom string
        versiontag = ''
        if etpData['versiontag']:
            versiontag = '#'+etpData['versiontag']

        foundid = self.isPackageAvailable(etpData['category']+"/"+etpData['name']+"-"+etpData['version']+versiontag)
        if (foundid < 0): # same atom doesn't exist in any branch
            return self.addPackage(etpData, revision = forcedRevision)
        else:
            return self.updatePackage(etpData, forcedRevision) # only when the same atom exists

    def retrieve_packages_to_remove(self, name, category, slot, branch, injected):
        removelist = set()

        # we need to find other packages with the same key and slot, and remove them
        if self.clientDatabase: # client database can't care about branch
            searchsimilar = self.searchPackagesByNameAndCategory(
                name = name,
                category = category,
                sensitive = True
            )
        else: # server supports multiple branches inside a db
            searchsimilar = self.searchPackagesByNameAndCategory(
                name = name,
                category = category,
                sensitive = True,
                branch = branch
            )

        if not injected:
            # read: if package has been injected, we'll skip
            # the removal of packages in the same slot, usually used server side btw
            for oldpkg in searchsimilar:
                # get the package slot
                idpackage = oldpkg[1]
                myslot = self.retrieveSlot(idpackage)
                isinjected = self.isInjected(idpackage)
                if isinjected:
                    continue
                    # we merely ignore packages with
                    # negative counters, since they're the injected ones
                if slot == myslot:
                    # remove!
                    removelist.add(idpackage)

        return removelist

    def addPackage(self, etpData, revision = -1):

        self.checkReadOnly()
        self.live_cache.clear()

        if revision == -1:
            try:
                revision = int(etpData['revision'])
            except (KeyError, ValueError):
                etpData['revision'] = 0 # revision not specified
                revision = 0

        removelist = self.retrieve_packages_to_remove(
                        etpData['name'],
                        etpData['category'],
                        etpData['slot'],
                        etpConst['branch'],
                        etpData['injected']
        )
        for pkg in removelist:
            self.removePackage(pkg)

        # create new category if it doesn't exist
        catid = self.isCategoryAvailable(etpData['category'])
        if (catid == -1):
            # create category
            catid = self.addCategory(etpData['category'])


        # create new license if it doesn't exist
        licid = self.isLicenseAvailable(etpData['license'])
        if (licid == -1):
            # create category
            licid = self.addLicense(etpData['license'])

        # insert license information
        mylicenses = etpData['licensedata'].keys()
        for mylicense in mylicenses:
            found = self.isLicensedataKeyAvailable(mylicense)
            if not found:
                text = etpData['licensedata'][mylicense]
                self.cursor.execute(
                    'INSERT into licensedata VALUES '
                    '(?,?,?)'
                    , (	mylicense,
                            buffer(text),
                            0,
                ))

        # look for configured versiontag
        versiontag = ""
        if (etpData['versiontag']):
            versiontag = "#"+etpData['versiontag']

        trigger = 0
        if etpData['trigger']:
            trigger = 1

        # baseinfo
        pkgatom = etpData['category']+"/"+etpData['name']+"-"+etpData['version']+versiontag
        # create new idflag if it doesn't exist
        idflags = self.areCompileFlagsAvailable(etpData['chost'],etpData['cflags'],etpData['cxxflags'])
        if (idflags == -1):
            # create category
            idflags = self.addCompileFlags(etpData['chost'],etpData['cflags'],etpData['cxxflags'])

        self.cursor.execute(
                'INSERT into baseinfo VALUES '
                '(NULL,?,?,?,?,?,?,?,?,?,?,?)'
                , (	pkgatom,
                        catid,
                        etpData['name'],
                        etpData['version'],
                        etpData['versiontag'],
                        revision,
                        etpData['branch'],
                        etpData['slot'],
                        licid,
                        etpData['etpapi'],
                        trigger,
                        )
        )
        idpackage = self.cursor.lastrowid

        # extrainfo
        self.cursor.execute(
                'INSERT into extrainfo VALUES '
                '(?,?,?,?,?,?,?,?)'
                , (	idpackage,
                        etpData['description'],
                        etpData['homepage'],
                        etpData['download'],
                        etpData['size'],
                        idflags,
                        etpData['digest'],
                        etpData['datecreation'],
                        )
        )
        ### other information iserted below are not as critical as these above

        # content, a list
        self.insertContent(idpackage,etpData['content'])

        etpData['counter'] = int(etpData['counter']) # cast to integer
        if etpData['counter'] != -1 and not (etpData['injected']):

            if etpData['counter'] <= -2:
                # special cases
                etpData['counter'] = self.getNewNegativeCounter()

            try:
                self.cursor.execute(
                'INSERT into counters VALUES '
                '(?,?,?)'
                , ( etpData['counter'],
                    idpackage,
                    etpData['branch'],
                    )
                )
            except dbapi2.IntegrityError: # we have a PRIMARY KEY we need to remove
                self.migrateCountersTable()
                self.cursor.execute(
                'INSERT into counters VALUES '
                '(?,?,?)'
                , ( etpData['counter'],
                    idpackage,
                    etpData['branch'],
                    )
                )
            except:
                if self.dbname == etpConst['clientdbid']: # force only for client database
                    if self.doesTableExist("counters"):
                        raise
                    self.cursor.execute(
                    'INSERT into counters VALUES '
                    '(?,?,?)'
                    , ( etpData['counter'],
                        idpackage,
                        etpData['branch'],
                        )
                    )
                elif self.dbname.startswith(etpConst['serverdbid']):
                    raise

        # on disk size
        self.cursor.execute(
        'INSERT into sizes VALUES '
        '(?,?)'
        , (	idpackage,
            etpData['disksize'],
            )
        )

        # trigger blob
        self.cursor.execute(
        'INSERT into triggers VALUES '
        '(?,?)'
        , (	idpackage,
            buffer(etpData['trigger']),
        ))

        # eclasses table
        for var in etpData['eclasses']:

            idclass = self.isEclassAvailable(var)
            if (idclass == -1):
                # create eclass
                idclass = self.addEclass(var)

            self.cursor.execute(
                'INSERT into eclasses VALUES '
                '(?,?)'
                , (	idpackage,
                        idclass,
                        )
            )

        # needed table
        for mydata in etpData['needed']:

            needed = mydata[0]
            elfclass = mydata[1]
            idneeded = self.isNeededAvailable(needed)

            if (idneeded == -1):
                # create eclass
                idneeded = self.addNeeded(needed)

            self.cursor.execute(
                'INSERT into needed VALUES '
                '(?,?,?)'
                , (	idpackage,
                        idneeded,
                        elfclass,
                        )
            )

        # dependencies, a list
        self.insertDependencies(idpackage, etpData['dependencies'])

        # provide
        for atom in etpData['provide']:
            self.cursor.execute(
                'INSERT into provide VALUES '
                '(?,?)'
                , (	idpackage,
                        atom,
                        )
            )

        # injected?
        if etpData['injected']:
            self.setInjected(idpackage)

        # compile messages
        for message in etpData['messages']:
            self.cursor.execute(
            'INSERT into messages VALUES '
            '(?,?)'
            , (	idpackage,
                    message,
                    )
            )

        # is it a system package?
        if etpData['systempackage']:
            self.cursor.execute(
                'INSERT into systempackages VALUES '
                '(?)'
                , (	idpackage,
                        )
            )

        # create new protect if it doesn't exist
        idprotect = self.isProtectAvailable(etpData['config_protect'])
        if (idprotect == -1):
            # create category
            idprotect = self.addProtect(etpData['config_protect'])
        # fill configprotect
        self.cursor.execute(
                'INSERT into configprotect VALUES '
                '(?,?)'
                , (	idpackage,
                        idprotect,
                        )
        )

        idprotect = self.isProtectAvailable(etpData['config_protect_mask'])
        if (idprotect == -1):
            # create category
            idprotect = self.addProtect(etpData['config_protect_mask'])
        # fill configprotect
        self.cursor.execute(
                'INSERT into configprotectmask VALUES '
                '(?,?)'
                , (	idpackage,
                        idprotect,
                        )
        )

        # conflicts, a list
        for conflict in etpData['conflicts']:
            self.cursor.execute(
                'INSERT into conflicts VALUES '
                '(?,?)'
                , (	idpackage,
                        conflict,
                        )
            )

        # mirrorlinks, always update the table
        for mirrordata in etpData['mirrorlinks']:
            mirrorname = mirrordata[0]
            mirrorlist = mirrordata[1]
            # remove old
            self.removeMirrorEntries(mirrorname)
            # add new
            self.addMirrors(mirrorname,mirrorlist)

        # sources, a list
        for source in etpData['sources']:

            if (not source) or (source == "") or (not self.entropyTools.is_valid_string(source)):
                continue

            idsource = self.isSourceAvailable(source)
            if (idsource == -1):
                # create category
                idsource = self.addSource(source)

            self.cursor.execute(
                'INSERT into sources VALUES '
                '(?,?)'
                , (	idpackage,
                        idsource,
                        )
            )

        # useflags, a list
        for flag in etpData['useflags']:

            iduseflag = self.isUseflagAvailable(flag)
            if (iduseflag == -1):
                # create category
                iduseflag = self.addUseflag(flag)

            self.cursor.execute(
                'INSERT into useflags VALUES '
                '(?,?)'
                , (	idpackage,
                        iduseflag,
                        )
            )

        # create new keyword if it doesn't exist
        for key in etpData['keywords']:

            idkeyword = self.isKeywordAvailable(key)
            if (idkeyword == -1):
                # create category
                idkeyword = self.addKeyword(key)

            self.cursor.execute(
                'INSERT into keywords VALUES '
                '(?,?)'
                , (	idpackage,
                        idkeyword,
                        )
            )

        self.clearCache()
        self.commitChanges()

        ### RSS Atom support
        ### dictionary will be elaborated by activator
        if etpConst['rss-feed'] and not self.clientDatabase:
            rssAtom = pkgatom+"~"+str(revision)
            # store addPackage action
            rssObj = dumpTools.loadobj(etpConst['rss-dump-name'])
            global etpRSSMessages
            if rssObj:
                etpRSSMessages = rssObj.copy()
            if not isinstance(etpRSSMessages,dict):
                etpRSSMessages = {}
            if not etpRSSMessages.has_key('added'):
                etpRSSMessages['added'] = {}
            if not etpRSSMessages.has_key('removed'):
                etpRSSMessages['removed'] = {}
            if rssAtom in etpRSSMessages['removed']:
                del etpRSSMessages['removed'][rssAtom]
            etpRSSMessages['added'][rssAtom] = {}
            etpRSSMessages['added'][rssAtom]['description'] = etpData['description']
            etpRSSMessages['added'][rssAtom]['homepage'] = etpData['homepage']
            etpRSSMessages['light'][rssAtom] = {}
            etpRSSMessages['light'][rssAtom]['description'] = etpData['description']
            # save
            dumpTools.dumpobj(etpConst['rss-dump-name'],etpRSSMessages)

        # Update category description
        if not self.clientDatabase:
            mycategory = etpData['category']
            descdata = {}
            try:
                descdata = self.get_category_description_from_disk(mycategory)
            except (IOError,OSError,EOFError):
                pass
            if descdata:
                self.setCategoryDescription(mycategory,descdata)

        return idpackage, revision, etpData

    # Update already available atom in db
    # returns True,revision if the package has been updated
    # returns False,revision if not
    def updatePackage(self, etpData, forcedRevision = -1):

        self.checkReadOnly()

        # build atom string
        versiontag = ''
        if etpData['versiontag']:
            versiontag = '#'+etpData['versiontag']
        pkgatom = etpData['category'] + "/" + etpData['name'] + "-" + etpData['version']+versiontag

        # for client database - the atom if present, must be overwritten with the new one regardless its branch
        if (self.clientDatabase):

            atomid = self.isPackageAvailable(pkgatom)
            if atomid > -1:
                self.removePackage(atomid)

            return self.addPackage(etpData, revision = forcedRevision)

        else:
            # update package in etpData['branch']
            # get its package revision
            idpackage = self.getIDPackage(pkgatom,etpData['branch'])
            if (forcedRevision == -1):
                if (idpackage != -1):
                    curRevision = self.retrieveRevision(idpackage)
                else:
                    curRevision = 0
            else:
                curRevision = forcedRevision

            if (idpackage != -1): # remove old package in branch
                self.removePackage(idpackage)
                if (forcedRevision == -1):
                    curRevision += 1

            # add the new one
            return self.addPackage(etpData, revision = curRevision)


    def removePackage(self,idpackage):

        self.checkReadOnly()
        self.live_cache.clear()

        ### RSS Atom support
        ### dictionary will be elaborated by activator
        if etpConst['rss-feed'] and not self.clientDatabase:
            # store addPackage action
            rssObj = dumpTools.loadobj(etpConst['rss-dump-name'])
            global etpRSSMessages
            if rssObj:
                etpRSSMessages = rssObj.copy()
            rssAtom = self.retrieveAtom(idpackage)
            rssRevision = self.retrieveRevision(idpackage)
            rssAtom += "~"+str(rssRevision)
            if not isinstance(etpRSSMessages,dict):
                etpRSSMessages = {}
            if not etpRSSMessages.has_key('added'):
                etpRSSMessages['added'] = {}
            if not etpRSSMessages.has_key('removed'):
                etpRSSMessages['removed'] = {}
            if rssAtom in etpRSSMessages['added']:
                del etpRSSMessages['added'][rssAtom]
            etpRSSMessages['removed'][rssAtom] = {}
            try:
                etpRSSMessages['removed'][rssAtom]['description'] = self.retrieveDescription(idpackage)
            except:
                etpRSSMessages['removed'][rssAtom]['description'] = "N/A"
            try:
                etpRSSMessages['removed'][rssAtom]['homepage'] = self.retrieveHomepage(idpackage)
            except:
                etpRSSMessages['removed'][rssAtom]['homepage'] = ""
            # save
            dumpTools.dumpobj(etpConst['rss-dump-name'],etpRSSMessages)

        idpackage = str(idpackage)
        # baseinfo
        self.cursor.execute('DELETE FROM baseinfo WHERE idpackage = '+idpackage)
        # extrainfo
        self.cursor.execute('DELETE FROM extrainfo WHERE idpackage = '+idpackage)
        # content
        self.cursor.execute('DELETE FROM content WHERE idpackage = '+idpackage)
        # dependencies
        self.cursor.execute('DELETE FROM dependencies WHERE idpackage = '+idpackage)
        # provide
        self.cursor.execute('DELETE FROM provide WHERE idpackage = '+idpackage)
        # conflicts
        self.cursor.execute('DELETE FROM conflicts WHERE idpackage = '+idpackage)
        # protect
        self.cursor.execute('DELETE FROM configprotect WHERE idpackage = '+idpackage)
        # protect_mask
        self.cursor.execute('DELETE FROM configprotectmask WHERE idpackage = '+idpackage)
        # sources
        self.cursor.execute('DELETE FROM sources WHERE idpackage = '+idpackage)
        # useflags
        self.cursor.execute('DELETE FROM useflags WHERE idpackage = '+idpackage)
        # keywords
        self.cursor.execute('DELETE FROM keywords WHERE idpackage = '+idpackage)

        #
        # WARNING: exception won't be handled anymore with 1.0
        #

        try:
            # messages
            self.cursor.execute('DELETE FROM messages WHERE idpackage = '+idpackage)
        except:
            pass
        try:
            # systempackage
            self.cursor.execute('DELETE FROM systempackages WHERE idpackage = '+idpackage)
        except:
            pass
        try:
            # counter
            self.cursor.execute('DELETE FROM counters WHERE idpackage = '+idpackage)
        except:
            if (self.dbname == etpConst['clientdbid']) or self.dbname.startswith(etpConst['serverdbid']):
                raise
        try:
            # on disk sizes
            self.cursor.execute('DELETE FROM sizes WHERE idpackage = '+idpackage)
        except:
            pass
        try:
            # eclasses
            self.cursor.execute('DELETE FROM eclasses WHERE idpackage = '+idpackage)
        except:
            pass
        try:
            # needed
            self.cursor.execute('DELETE FROM needed WHERE idpackage = '+idpackage)
        except:
            pass
        try:
            # triggers
            self.cursor.execute('DELETE FROM triggers WHERE idpackage = '+idpackage)
        except:
            pass
        try:
            # inject table
            self.cursor.execute('DELETE FROM injected WHERE idpackage = '+idpackage)
        except:
            pass

        # Remove from installedtable if exists
        self.removePackageFromInstalledTable(idpackage)
        # Remove from dependstable if exists
        self.removePackageFromDependsTable(idpackage)

        # Cleanups if at least one package has been removed
        self.cleanupUseflags()
        self.cleanupSources()
        self.cleanupEclasses()
        self.cleanupNeeded()
        self.cleanupDependencies()

        # clear caches
        self.clearCache()

        self.commitChanges()

    def removeMirrorEntries(self,mirrorname):
        self.cursor.execute('DELETE FROM mirrorlinks WHERE mirrorname = "'+mirrorname+'"')

    def addMirrors(self,mirrorname,mirrorlist):
        for x in mirrorlist:
            self.cursor.execute(
                'INSERT into mirrorlinks VALUES '
                '(?,?)', (mirrorname,x,)
            )

    def addCategory(self,category):
        self.cursor.execute(
                'INSERT into categories VALUES '
                '(NULL,?)', (category,)
        )
        # get info about inserted value and return
        cat = self.isCategoryAvailable(category)
        if cat != -1:
            return cat
        raise exceptionTools.CorruptionError("CorruptionError: I tried to insert a category but then, fetching it returned -1. There's something broken.")

    def addProtect(self,protect):
        self.cursor.execute(
                'INSERT into configprotectreference VALUES '
                '(NULL,?)', (protect,)
        )
        # get info about inserted value and return
        prt = self.isProtectAvailable(protect)
        if prt != -1:
            return prt
        raise exceptionTools.CorruptionError("CorruptionError: I tried to insert a protect but then, fetching it returned -1. There's something broken.")

    def addSource(self,source):
        self.cursor.execute(
                'INSERT into sourcesreference VALUES '
                '(NULL,?)', (source,)
        )
        # get info about inserted value and return
        src = self.isSourceAvailable(source)
        if src != -1:
            return src
        raise exceptionTools.CorruptionError("CorruptionError: I tried to insert a source but then, fetching it returned -1. There's something broken.")

    def addDependency(self,dependency):
        self.cursor.execute(
                'INSERT into dependenciesreference VALUES '
                '(NULL,?)', (dependency,)
        )
        # get info about inserted value and return
        dep = self.isDependencyAvailable(dependency)
        if dep != -1:
            return dep
        raise exceptionTools.CorruptionError("CorruptionError: I tried to insert a dependency but then, fetching it returned -1. There's something broken.")

    def addKeyword(self,keyword):
        self.cursor.execute(
                'INSERT into keywordsreference VALUES '
                '(NULL,?)', (keyword,)
        )
        # get info about inserted value and return
        key = self.isKeywordAvailable(keyword)
        if key != -1:
            return key
        raise exceptionTools.CorruptionError("CorruptionError: I tried to insert a keyword but then, fetching it returned -1. There's something broken.")

    def addUseflag(self,useflag):
        self.cursor.execute(
                'INSERT into useflagsreference VALUES '
                '(NULL,?)', (useflag,)
        )
        # get info about inserted value and return
        use = self.isUseflagAvailable(useflag)
        if use != -1:
            return use
        raise exceptionTools.CorruptionError("CorruptionError: I tried to insert a use flag but then, fetching it returned -1. There's something broken.")

    def addEclass(self,eclass):
        self.cursor.execute(
                'INSERT into eclassesreference VALUES '
                '(NULL,?)', (eclass,)
        )
        # get info about inserted value and return
        myclass = self.isEclassAvailable(eclass)
        if myclass != -1:
            return myclass
        raise exceptionTools.CorruptionError("CorruptionError: I tried to insert an eclass but then, fetching it returned -1. There's something broken.")

    def addNeeded(self,needed):
        self.cursor.execute(
                'INSERT into neededreference VALUES '
                '(NULL,?)', (needed,)
        )
        # get info about inserted value and return
        myneeded = self.isNeededAvailable(needed)
        if myneeded != -1:
            return myneeded
        raise exceptionTools.CorruptionError("CorruptionError: I tried to insert a needed library but then, fetching it returned -1. There's something broken.")

    def addLicense(self,pkglicense):
        if not self.entropyTools.is_valid_string(pkglicense):
            pkglicense = ' ' # workaround for broken license entries
        self.cursor.execute(
                'INSERT into licenses VALUES '
                '(NULL,?)', (pkglicense,)
        )
        # get info about inserted value and return
        lic = self.isLicenseAvailable(pkglicense)
        if lic != -1:
            return lic
        raise exceptionTools.CorruptionError("CorruptionError: I tried to insert a license but then, fetching it returned -1. There's something broken.")

    def addCompileFlags(self,chost,cflags,cxxflags):
        self.cursor.execute(
                'INSERT into flags VALUES '
                '(NULL,?,?,?)', (chost,cflags,cxxflags,)
        )
        # get info about inserted value and return
        idflag = self.areCompileFlagsAvailable(chost,cflags,cxxflags)
        if idflag != -1:
            return idflag
        raise exceptionTools.CorruptionError("CorruptionError: I tried to insert compile flags but then, fetching it returned -1. There's something broken.")

    def setInjected(self, idpackage):
        self.checkReadOnly()
        if not self.isInjected(idpackage):
            self.cursor.execute(
                'INSERT into injected VALUES '
                '(?)'
                , ( idpackage, )
            )
        self.commitChanges()

    # date expressed the unix way
    def setDateCreation(self, idpackage, date):
        self.checkReadOnly()
        self.cursor.execute('UPDATE extrainfo SET datecreation = (?) WHERE idpackage = (?)', (str(date),idpackage,))
        self.commitChanges()

    def setDigest(self, idpackage, digest):
        self.checkReadOnly()
        self.cursor.execute('UPDATE extrainfo SET digest = (?) WHERE idpackage = (?)', (digest,idpackage,))
        self.commitChanges()

    def setDownloadURL(self, idpackage, url):
        self.checkReadOnly()
        self.cursor.execute('UPDATE extrainfo SET download = (?) WHERE idpackage = (?)', (url,idpackage,))
        self.commitChanges()

    def setCategory(self, idpackage, category):
        self.checkReadOnly()
        # create new category if it doesn't exist
        catid = self.isCategoryAvailable(category)
        if (catid == -1):
            # create category
            catid = self.addCategory(category)
        self.cursor.execute('UPDATE baseinfo SET idcategory = (?) WHERE idpackage = (?)', (catid,idpackage,))
        self.commitChanges()

    def setCategoryDescription(self, category, description_data):
        self.checkReadOnly()
        self.cursor.execute('DELETE FROM categoriesdescription WHERE category = (?)', (category,))
        for locale in description_data:
            mydesc = description_data[locale]
            #if type(mydesc) is unicode:
            #    mydesc = mydesc.encode('raw_unicode_escape')
            self.cursor.execute('INSERT INTO categoriesdescription VALUES (?,?,?)', (category,locale,mydesc,))
        self.commitChanges()

    def setName(self, idpackage, name):
        self.checkReadOnly()
        self.cursor.execute('UPDATE baseinfo SET name = (?) WHERE idpackage = (?)', (name,idpackage,))
        self.commitChanges()

    def setDependency(self, iddependency, dependency):
        self.checkReadOnly()
        self.cursor.execute('UPDATE dependenciesreference SET dependency = (?) WHERE iddependency = (?)', (dependency,iddependency,))
        self.commitChanges()

    def setAtom(self, idpackage, atom):
        self.checkReadOnly()
        self.cursor.execute('UPDATE baseinfo SET atom = (?) WHERE idpackage = (?)', (atom,idpackage,))
        self.commitChanges()

    def setSlot(self, idpackage, slot):
        self.checkReadOnly()
        self.cursor.execute('UPDATE baseinfo SET slot = (?) WHERE idpackage = (?)', (slot,idpackage,))
        self.commitChanges()

    def removeLicensedata(self, license_name):
        if not self.doesTableExist("licensedata"):
            return
        self.cursor.execute('DELETE FROM licensedata WHERE licensename = (?)', (license_name,))

    def removeDependencies(self, idpackage):
        self.checkReadOnly()
        self.cursor.execute("DELETE FROM dependencies WHERE idpackage = (?)", (idpackage,))
        self.commitChanges()

    def insertDependencies(self, idpackage, depdata):

        dcache = set()
        for dep in depdata:

            if dep in dcache:
                continue

            iddep = self.isDependencyAvailable(dep)
            if (iddep == -1):
                # create category
                iddep = self.addDependency(dep)

            if type(depdata) is dict:
                deptype = depdata[dep]
            else:
                deptype = 0

            dcache.add(dep)

            self.cursor.execute(
                'INSERT into dependencies VALUES '
                '(?,?,?)'
                , (	idpackage,
                        iddep,
                        deptype,
                        )
            )

    def removeContent(self, idpackage):
        self.checkReadOnly()
        self.cursor.execute("DELETE FROM content WHERE idpackage = (?)", (idpackage,))
        self.commitChanges()

    def insertContent(self, idpackage, content):

        def myiter():
            for xfile in content:
                contenttype = content[xfile]
                if type(xfile) is unicode:
                    xfile = xfile.encode('raw_unicode_escape')
                yield (idpackage,xfile,contenttype,)

        self.cursor.executemany('INSERT INTO content VALUES (?,?,?)',myiter())

    def insertCounter(self, idpackage, counter, branch = None):
        self.checkReadOnly()
        if not branch:
            branch = etpConst['branch']
        self.cursor.execute('DELETE FROM counters WHERE counter = (?) OR idpackage = (?)', (counter,idpackage,))
        self.cursor.execute('INSERT INTO counters VALUES (?,?,?)', (counter,idpackage,branch,))
        self.commitChanges()

    def setTrashedCounter(self, counter):
        self.checkReadOnly()
        self.cursor.execute('DELETE FROM trashedcounters WHERE counter = (?)', (counter,))
        self.cursor.execute('INSERT INTO trashedcounters VALUES (?)', (counter,))
        self.commitChanges()

    def setCounter(self, idpackage, counter, branch = None):
        self.checkReadOnly()

        branchstring = ''
        insertdata = [counter,idpackage]
        if branch:
            branchstring = ', branch = (?)'
            insertdata.insert(1,branch)
        else:
            branch = etpConst['branch']

        try:
            self.cursor.execute('UPDATE counters SET counter = (?) '+branchstring+' WHERE idpackage = (?)', insertdata)
        except:
            if self.dbname == etpConst['clientdbid']:
                raise
        self.commitChanges()

    def contentDiff(self, idpackage, dbconn, dbconn_idpackage):
        self.checkReadOnly()
        self.connection.text_factory = lambda x: unicode(x, "raw_unicode_escape")
        # create a random table and fill
        randomtable = "cdiff"+str(self.entropyTools.getRandomNumber())
        self.cursor.execute('DROP TABLE IF EXISTS '+randomtable)
        self.cursor.execute('CREATE TEMPORARY TABLE '+randomtable+' ( file VARCHAR )')

        dbconn.connection.text_factory = lambda x: unicode(x, "raw_unicode_escape")
        dbconn.cursor.execute('select file from content where idpackage = (?)', (dbconn_idpackage,))
        xfile = dbconn.cursor.fetchone()
        while xfile:
            self.cursor.execute('INSERT INTO '+randomtable+' VALUES (?)', (xfile[0],))
            xfile = dbconn.cursor.fetchone()

        # now compare
        self.cursor.execute('SELECT file FROM content WHERE content.idpackage = (?) AND content.file NOT IN (SELECT file from '+randomtable+') ', (idpackage,))
        diff = self.fetchall2set(self.cursor.fetchall())
        self.cursor.execute('DROP TABLE IF EXISTS '+randomtable)
        return diff


    def cleanupUseflags(self):
        self.checkReadOnly()
        self.cursor.execute('delete from useflagsreference where idflag IN (select idflag from useflagsreference where idflag NOT in (select idflag from useflags))')
        self.commitChanges()

    def cleanupSources(self):
        self.checkReadOnly()
        self.cursor.execute('delete from sourcesreference where idsource IN (select idsource from sourcesreference where idsource NOT in (select idsource from sources))')
        self.commitChanges()

    def cleanupEclasses(self):
        self.checkReadOnly()
        self.cursor.execute('delete from eclassesreference where idclass IN (select idclass from eclassesreference where idclass NOT in (select idclass from eclasses))')
        self.commitChanges()

    def cleanupNeeded(self):
        self.checkReadOnly()
        self.cursor.execute('delete from neededreference where idneeded IN (select idneeded from neededreference where idneeded NOT in (select idneeded from needed))')
        self.commitChanges()

    def cleanupDependencies(self):
        self.checkReadOnly()
        self.cursor.execute('delete from dependenciesreference where iddependency IN (select iddependency from dependenciesreference where iddependency NOT in (select iddependency from dependencies))')
        self.commitChanges()

    def getNewNegativeCounter(self):
        counter = -2
        try:
            self.cursor.execute('SELECT min(counter) FROM counters')
            dbcounter = self.cursor.fetchone()
            mycounter = 0
            if dbcounter:
                mycounter = dbcounter[0]

            if mycounter >= -1:
                counter = -2
            else:
                counter = mycounter-1

        except:
            pass
        return counter

    def getApi(self):
        self.cursor.execute('SELECT max(etpapi) FROM baseinfo')
        api = self.cursor.fetchone()
        if api: api = api[0]
        else: api = -1
        return api

    def getCategory(self, idcategory):
        self.cursor.execute('SELECT category from categories WHERE idcategory = (?)', (idcategory,))
        cat = self.cursor.fetchone()
        if cat: cat = cat[0]
        return cat

    def get_category_description_from_disk(self, category):
        if not self.ServiceInterface:
            return {}
        return self.ServiceInterface.SpmService.get_category_description_data(category)

    def getIDPackage(self, atom, branch = etpConst['branch']):
        self.cursor.execute('SELECT idpackage FROM baseinfo WHERE atom = "'+atom+'" AND branch = "'+branch+'"')
        idpackage = -1
        idpackage = self.cursor.fetchone()
        if idpackage:
            idpackage = idpackage[0]
        else:
            idpackage = -1
        return idpackage

    def getIDPackageFromDownload(self, file, branch = etpConst['branch']):
        self.cursor.execute('SELECT baseinfo.idpackage FROM content,baseinfo WHERE content.file = (?) and baseinfo.branch = (?)', (file,branch,))
        idpackage = self.cursor.fetchone()
        if idpackage:
            idpackage = idpackage[0]
        else:
            idpackage = -1
        return idpackage

    def getIDPackagesFromFile(self, file):
        self.cursor.execute('SELECT idpackage FROM content WHERE file = "'+file+'"')
        idpackages = []
        for row in self.cursor:
            idpackages.append(row[0])
        return idpackages

    def getIDCategory(self, category):
        self.cursor.execute('SELECT "idcategory" FROM categories WHERE category = "'+str(category)+'"')
        idcat = -1
        for row in self.cursor:
            idcat = int(row[0])
            break
        return idcat

    def getStrictData(self, idpackage):
        self.cursor.execute('SELECT categories.category || "/" || baseinfo.name,baseinfo.slot,baseinfo.version,baseinfo.versiontag,baseinfo.revision,baseinfo.atom FROM baseinfo,categories WHERE baseinfo.idpackage = (?) and baseinfo.idcategory = categories.idcategory', (idpackage,))
        return self.cursor.fetchone()

    def getScopeData(self, idpackage):
        self.cursor.execute("""
                SELECT 
                        baseinfo.atom,
                        categories.category,
                        baseinfo.name,
                        baseinfo.version,
                        baseinfo.slot,
                        baseinfo.versiontag,
                        baseinfo.revision,
                        baseinfo.branch
                FROM 
                        baseinfo,
                        categories
                WHERE 
                        baseinfo.idpackage = (?)
                        and baseinfo.idcategory = categories.idcategory
        """, (idpackage,))
        return self.cursor.fetchone()

    def getBaseData(self,idpackage):

        sql = """
                SELECT 
                        baseinfo.atom,
                        baseinfo.name,
                        baseinfo.version,
                        baseinfo.versiontag,
                        extrainfo.description,
                        categories.category,
                        flags.chost,
                        flags.cflags,
                        flags.cxxflags,
                        extrainfo.homepage,
                        licenses.license,
                        baseinfo.branch,
                        extrainfo.download,
                        extrainfo.digest,
                        baseinfo.slot,
                        baseinfo.etpapi,
                        extrainfo.datecreation,
                        extrainfo.size,
                        baseinfo.revision
                FROM 
                        baseinfo,
                        extrainfo,
                        categories,
                        flags,
                        licenses
                WHERE 
                        baseinfo.idpackage = '"""+str(idpackage)+"""' 
                        and baseinfo.idpackage = extrainfo.idpackage 
                        and baseinfo.idcategory = categories.idcategory 
                        and extrainfo.idflags = flags.idflags
                        and baseinfo.idlicense = licenses.idlicense
        """
        self.cursor.execute(sql)
        return self.cursor.fetchone()

    def getTriggerInfo(self, idpackage):
        data = {}

        mydata = self.getScopeData(idpackage)

        data['atom'] = mydata[0]
        data['category'] = mydata[1]
        data['name'] = mydata[2]
        data['version'] = mydata[3]
        data['versiontag'] = mydata[5]
        flags = self.retrieveCompileFlags(idpackage)
        data['chost'] = flags[0]
        data['cflags'] = flags[1]
        data['cxxflags'] = flags[2]

        data['trigger'] = self.retrieveTrigger(idpackage)
        data['eclasses'] = self.retrieveEclasses(idpackage)
        data['content'] = self.retrieveContent(idpackage)

        return data

    def getPackageData(self, idpackage, get_content = True):
        data = {}

        mydata = self.getBaseData(idpackage)

        data['name'] = mydata[1]
        data['version'] = mydata[2]
        data['versiontag'] = mydata[3]
        data['description'] = mydata[4]
        data['category'] = mydata[5]

        data['chost'] = mydata[6]
        data['cflags'] = mydata[7]
        data['cxxflags'] = mydata[8]

        data['homepage'] = mydata[9]
        data['useflags'] = self.retrieveUseflags(idpackage)
        data['license'] = mydata[10]

        data['keywords'] = self.retrieveKeywords(idpackage)

        data['branch'] = mydata[11]
        data['download'] = mydata[12]
        data['digest'] = mydata[13]
        data['sources'] = self.retrieveSources(idpackage)
        data['counter'] = self.retrieveCounter(idpackage) # cannot insert into the sql above
        data['messages'] = self.retrieveMessages(idpackage)
        data['trigger'] = self.retrieveTrigger(idpackage) #FIXME: needed for now because of new column

        if (self.isSystemPackage(idpackage)):
            data['systempackage'] = 'xxx'
        else:
            data['systempackage'] = ''

        data['config_protect'] = self.retrieveProtect(idpackage)
        data['config_protect_mask'] = self.retrieveProtectMask(idpackage)

        data['eclasses'] = self.retrieveEclasses(idpackage)
        data['needed'] = self.retrieveNeeded(idpackage, extended = True)

        mirrornames = set()
        for x in data['sources']:
            if x.startswith("mirror://"):
                mirrorname = x.split("/")[2]
                mirrornames.add(mirrorname)
        data['mirrorlinks'] = []
        for mirror in mirrornames:
            mirrorlinks = self.retrieveMirrorInfo(mirror)
            data['mirrorlinks'].append([mirror,mirrorlinks])

        data['slot'] = mydata[14]
        data['injected'] = self.isInjected(idpackage)
        data['content'] = {}
        if get_content:
            mycontent = self.retrieveContent(idpackage, extended = True)
            for xfile,filetype in mycontent:
                data['content'][xfile] = filetype

        data['dependencies'] = {}
        depdata = self.retrieveDependencies(idpackage, extended = True)
        for dep,deptype in depdata:
            data['dependencies'][dep] = deptype
        data['provide'] = self.retrieveProvide(idpackage)
        data['conflicts'] = self.retrieveConflicts(idpackage)

        data['etpapi'] = mydata[15]
        data['datecreation'] = mydata[16]
        data['size'] = mydata[17]
        data['revision'] = mydata[18]
        # cannot do this too, for backward compat
        data['disksize'] = self.retrieveOnDiskSize(idpackage)

        data['licensedata'] = self.retrieveLicensedata(idpackage)

        return data

    def fetchall2set(self, item):
        mycontent = set()
        for x in item:
            mycontent |= set(x)
        return mycontent

    def fetchall2list(self, item):
        content = []
        for x in item:
            content += list(x)
        return content

    def fetchone2list(self, item):
        return list(item)

    def fetchone2set(self, item):
        return set(item)

    def clearCache(self, depends = False):
        self.live_cache.clear()
        def do_clear(name):
            dump_path = os.path.join(etpConst['dumpstoragedir'],name)
            dump_dir = os.path.dirname(dump_path)
            if os.path.isdir(dump_dir):
                for item in os.listdir(dump_dir):
                    item = os.path.join(dump_dir,item)
                    if os.path.isfile(item):
                        os.remove(item)
        do_clear(etpCache['dbMatch']+"/"+self.dbname+"/")
        do_clear(etpCache['dbSearch']+"/"+self.dbname+"/")
        if depends:
            do_clear(etpCache['depends_tree'])
            do_clear(etpCache['dep_tree'])
            do_clear(etpCache['filter_satisfied_deps'])

    def fetchSearchCache(self, key, function, extra_hash = 0):
        if self.xcache:

            c_hash = str(hash(function)) + str(extra_hash)
            c_match = str(key)
            try:
                cached = dumpTools.loadobj(etpCache['dbSearch']+"/"+self.dbname+"/"+c_match+"/"+c_hash)
                if cached != None:
                    return cached
            except EOFError:
                pass

    def storeSearchCache(self, key, function, search_cache_data, extra_hash = 0):
        if self.xcache:
            c_hash = str(hash(function)) + str(extra_hash)
            c_match = str(key)
            try:
                sperms = False
                if not os.path.isdir(os.path.join(etpConst['dumpstoragedir'],etpCache['dbSearch'],self.dbname)):
                    sperms = True
                elif not os.path.isdir(os.path.join(etpConst['dumpstoragedir'],etpCache['dbSearch'],self.dbname,c_match)):
                    sperms = True
                dumpTools.dumpobj(etpCache['dbSearch']+"/"+self.dbname+"/"+c_match+"/"+c_hash,search_cache_data)
                if sperms:
                    const_setup_perms(os.path.join(etpConst['dumpstoragedir'],etpCache['dbSearch']),etpConst['entropygid'])
            except IOError:
                pass

    def retrieveRepositoryUpdatesDigest(self, repository):
        if not self.doesTableExist("treeupdates"):
            return -1
        self.cursor.execute('SELECT digest FROM treeupdates WHERE repository = (?)', (repository,))
        mydigest = self.cursor.fetchone()
        if mydigest:
            return mydigest[0]
        else:
            return -1

    def listAllTreeUpdatesActions(self, no_ids_repos = False):
        if no_ids_repos:
            self.cursor.execute('SELECT command,branch,date FROM treeupdatesactions')
        else:
            self.cursor.execute('SELECT * FROM treeupdatesactions')
        return self.cursor.fetchall()

    def retrieveTreeUpdatesActions(self, repository, forbranch = etpConst['branch']):
        if not self.doesTableExist("treeupdatesactions"):
            return set()
        self.cursor.execute('SELECT command FROM treeupdatesactions where repository = (?) and branch = (?) order by date', (repository,forbranch))
        return self.fetchall2list(self.cursor.fetchall())

    # mainly used to restore a previous table, used by reagent in --initialize
    def bumpTreeUpdatesActions(self, updates):
        self.checkReadOnly()
        for update in updates:
            self.cursor.execute('INSERT INTO treeupdatesactions VALUES (?,?,?,?,?)', update)
        self.commitChanges()

    def removeTreeUpdatesActions(self, repository):
        self.checkReadOnly()
        self.cursor.execute('DELETE FROM treeupdatesactions WHERE repository = (?)', (repository,))
        self.commitChanges()

    def insertTreeUpdatesActions(self, updates, repository):
        self.checkReadOnly()
        for update in updates:
            update = list(update)
            update.insert(0,repository)
            self.cursor.execute('INSERT INTO treeupdatesactions VALUES (NULL,?,?,?,?)', update)
        self.commitChanges()

    def setRepositoryUpdatesDigest(self, repository, digest):
        self.checkReadOnly()
        self.cursor.execute('DELETE FROM treeupdates where repository = (?)', (repository,)) # doing it for safety
        self.cursor.execute('INSERT INTO treeupdates VALUES (?,?)', (repository,digest,))
        self.commitChanges()

    def addRepositoryUpdatesActions(self, repository, actions, forbranch = etpConst['branch']):
        self.checkReadOnly()
        mytime = str(self.entropyTools.getCurrentUnixTime())
        for command in actions:
            self.cursor.execute('INSERT INTO treeupdatesactions VALUES (NULL,?,?,?,?)', (repository,command,forbranch,mytime,))
        self.commitChanges()

    def retrieveAtom(self, idpackage):
        self.cursor.execute('SELECT atom FROM baseinfo WHERE idpackage = (?)', (idpackage,))
        atom = self.cursor.fetchone()
        if atom:
            return atom[0]

    def retrieveBranch(self, idpackage):
        self.cursor.execute('SELECT branch FROM baseinfo WHERE idpackage = (?)', (idpackage,))
        br = self.cursor.fetchone()
        if br:
            return br[0]

    def retrieveTrigger(self, idpackage):
        self.cursor.execute('SELECT data FROM triggers WHERE idpackage = (?)', (idpackage,))
        trigger = self.cursor.fetchone()
        if trigger:
            trigger = trigger[0]
        else:
            trigger = ''
        return trigger

    def retrieveDownloadURL(self, idpackage):
        self.cursor.execute('SELECT download FROM extrainfo WHERE idpackage = (?)', (idpackage,))
        download = self.cursor.fetchone()
        if download:
            return download[0]

    def retrieveDescription(self, idpackage):
        self.cursor.execute('SELECT description FROM extrainfo WHERE idpackage = (?)', (idpackage,))
        description = self.cursor.fetchone()
        if description:
            return description[0]

    def retrieveHomepage(self, idpackage):
        self.cursor.execute('SELECT homepage FROM extrainfo WHERE idpackage = (?)', (idpackage,))
        home = self.cursor.fetchone()
        if home:
            return home[0]

    def retrieveCounter(self, idpackage):
        counter = -1
        self.cursor.execute('SELECT counter FROM counters WHERE idpackage = (?)', (idpackage,))
        mycounter = self.cursor.fetchone()
        if mycounter:
            return mycounter[0]
        return counter

    def retrieveMessages(self, idpackage):
        messages = []
        try:
            self.cursor.execute('SELECT message FROM messages WHERE idpackage = (?)', (idpackage,))
            messages = self.fetchall2list(self.cursor.fetchall())
        except:
            pass
        return messages

    # in bytes
    def retrieveSize(self, idpackage):
        self.cursor.execute('SELECT size FROM extrainfo WHERE idpackage = (?)', (idpackage,))
        size = self.cursor.fetchone()
        if size:
            return size[0]

    # in bytes
    def retrieveOnDiskSize(self, idpackage):
        self.cursor.execute('SELECT size FROM sizes WHERE idpackage = (?)', (idpackage,))
        size = self.cursor.fetchone() # do not use [0]!
        if not size:
            size = 0
        else:
            size = size[0]
        return size

    def retrieveDigest(self, idpackage):
        self.cursor.execute('SELECT digest FROM extrainfo WHERE idpackage = (?)', (idpackage,))
        digest = self.cursor.fetchone()
        if digest:
            return digest[0]

    def retrieveName(self, idpackage):
        self.cursor.execute('SELECT name FROM baseinfo WHERE idpackage = (?)', (idpackage,))
        name = self.cursor.fetchone()
        if name:
            return name[0]

    def retrieveKeySlot(self, idpackage):
        self.cursor.execute('SELECT categories.category || "/" || baseinfo.name,baseinfo.slot FROM baseinfo,categories WHERE baseinfo.idpackage = (?) and baseinfo.idcategory = categories.idcategory', (idpackage,))
        data = self.cursor.fetchone()
        return data

    def retrieveVersion(self, idpackage):
        self.cursor.execute('SELECT version FROM baseinfo WHERE idpackage = (?)', (idpackage,))
        ver = self.cursor.fetchone()
        if ver:
            return ver[0]

    def retrieveRevision(self, idpackage):
        self.cursor.execute('SELECT revision FROM baseinfo WHERE idpackage = (?)', (idpackage,))
        rev = self.cursor.fetchone()
        if rev:
            return rev[0]

    def retrieveDateCreation(self, idpackage):
        self.cursor.execute('SELECT datecreation FROM extrainfo WHERE idpackage = (?)', (idpackage,))
        date = self.cursor.fetchone()
        if date:
            return date[0]

    def retrieveApi(self, idpackage):
        self.cursor.execute('SELECT etpapi FROM baseinfo WHERE idpackage = (?)', (idpackage,))
        api = self.cursor.fetchone()
        if api:
            return api[0]

    def retrieveUseflags(self, idpackage):
        self.cursor.execute('SELECT flagname FROM useflags,useflagsreference WHERE useflags.idpackage = (?) and useflags.idflag = useflagsreference.idflag', (idpackage,))
        return self.fetchall2set(self.cursor.fetchall())

    def retrieveEclasses(self, idpackage):
        self.cursor.execute('SELECT classname FROM eclasses,eclassesreference WHERE eclasses.idpackage = (?) and eclasses.idclass = eclassesreference.idclass', (idpackage,))
        return self.fetchall2set(self.cursor.fetchall())

    def retrieveNeeded(self, idpackage, extended = False, format = False):

        if extended and not self.doesColumnInTableExist("needed","elfclass"):
            if format:
                return {}
            else:
                return []

        if extended:
            self.cursor.execute('SELECT library,elfclass FROM needed,neededreference WHERE needed.idpackage = (?) and needed.idneeded = neededreference.idneeded', (idpackage,))
            needed = self.cursor.fetchall()
        else:
            self.cursor.execute('SELECT library FROM needed,neededreference WHERE needed.idpackage = (?) and needed.idneeded = neededreference.idneeded', (idpackage,))
            needed = self.fetchall2set(self.cursor.fetchall())

        if extended and format:
            data = {}
            for lib,elfclass in needed:
                data[lib] = elfclass
            needed = data

        return needed

    def retrieveConflicts(self, idpackage):
        self.cursor.execute('SELECT conflict FROM conflicts WHERE idpackage = (?)', (idpackage,))
        return self.fetchall2set(self.cursor.fetchall())

    def retrieveProvide(self, idpackage):
        self.cursor.execute('SELECT atom FROM provide WHERE idpackage = (?)', (idpackage,))
        return self.fetchall2set(self.cursor.fetchall())

    def retrieveDependenciesList(self, idpackage):
        deps = self.retrieveDependencies(idpackage)
        conflicts = self.retrieveConflicts(idpackage)
        for x in conflicts:
            if x[0] != "!":
                x = "!"+x
            deps.add(x)
        return deps

    def retrieveDependencies(self, idpackage, extended = False, deptype = None):

        searchdata = [idpackage]

        depstring = ''
        if deptype != None:
            depstring = ' and dependencies.type = (?)'
            searchdata.append(deptype)

        if extended:
            self.cursor.execute('SELECT dependenciesreference.dependency,dependencies.type FROM dependencies,dependenciesreference WHERE dependencies.idpackage = (?) and dependencies.iddependency = dependenciesreference.iddependency'+depstring, searchdata)
            deps = self.cursor.fetchall()
        else:
            self.cursor.execute('SELECT dependenciesreference.dependency FROM dependencies,dependenciesreference WHERE dependencies.idpackage = (?) and dependencies.iddependency = dependenciesreference.iddependency'+depstring, searchdata)
            deps = self.fetchall2set(self.cursor.fetchall())

        return deps

    def retrieveIdDependencies(self, idpackage):
        self.cursor.execute('SELECT iddependency FROM dependencies WHERE idpackage = (?)', (idpackage,))
        return self.fetchall2set(self.cursor.fetchall())

    def retrieveDependencyFromIddependency(self, iddependency):
        self.cursor.execute('SELECT dependency FROM dependenciesreference WHERE iddependency = (?)', (iddependency,))
        dep = self.cursor.fetchone()
        if dep: dep = dep[0]
        return dep

    def retrieveKeywords(self, idpackage):
        self.cursor.execute('SELECT keywordname FROM keywords,keywordsreference WHERE keywords.idpackage = (?) and keywords.idkeyword = keywordsreference.idkeyword', (idpackage,))
        return self.fetchall2set(self.cursor.fetchall())

    def retrieveProtect(self, idpackage):
        self.cursor.execute('SELECT protect FROM configprotect,configprotectreference WHERE configprotect.idpackage = (?) and configprotect.idprotect = configprotectreference.idprotect', (idpackage,))
        protect = self.cursor.fetchone()
        if not protect:
            protect = ''
        else:
            protect = protect[0]
        return protect

    def retrieveProtectMask(self, idpackage):
        self.cursor.execute('SELECT protect FROM configprotectmask,configprotectreference WHERE idpackage = (?) and configprotectmask.idprotect= configprotectreference.idprotect', (idpackage,))
        protect = self.cursor.fetchone()
        if not protect:
            protect = ''
        else:
            protect = protect[0]
        return protect

    def retrieveSources(self, idpackage):
        self.cursor.execute('SELECT sourcesreference.source FROM sources,sourcesreference WHERE idpackage = (?) and sources.idsource = sourcesreference.idsource', (idpackage,))
        return self.fetchall2set(self.cursor.fetchall())

    def retrieveContent(self, idpackage, extended = False, contentType = None):

        # like portage does
        self.connection.text_factory = lambda x: unicode(x, "raw_unicode_escape")

        extstring = ''
        if extended:
            extstring = ",type"

        searchkeywords = [idpackage]
        contentstring = ''
        if contentType:
            searchkeywords.append(contentType)
            contentstring = ' and type = (?)'

        self.cursor.execute('SELECT file'+extstring+' FROM content WHERE idpackage = (?) '+contentstring, searchkeywords)

        if extended:
            fl = self.cursor.fetchall()
        else:
            fl = self.fetchall2set(self.cursor.fetchall())

        return fl

    def retrieveSlot(self, idpackage):
        self.cursor.execute('SELECT slot FROM baseinfo WHERE idpackage = (?)', (idpackage,))
        slot = self.cursor.fetchone()
        if slot:
            return slot[0]

    def retrieveVersionTag(self, idpackage):
        self.cursor.execute('SELECT versiontag FROM baseinfo WHERE idpackage = (?)', (idpackage,))
        vtag = self.cursor.fetchone()
        if vtag:
            return vtag[0]

    def retrieveMirrorInfo(self, mirrorname):
        self.cursor.execute('SELECT mirrorlink FROM mirrorlinks WHERE mirrorname = (?)', (mirrorname,))
        mirrorlist = self.fetchall2set(self.cursor.fetchall())
        return mirrorlist

    def retrieveCategory(self, idpackage):
        self.cursor.execute('SELECT category FROM baseinfo,categories WHERE baseinfo.idpackage = (?) and baseinfo.idcategory = categories.idcategory', (idpackage,))
        cat = self.cursor.fetchone()
        if cat:
            return cat[0]

    def retrieveCategoryDescription(self, category):
        data = {}
        if not self.doesTableExist("categoriesdescription"):
            return data
        #self.connection.text_factory = lambda x: unicode(x, "raw_unicode_escape")
        self.cursor.execute('SELECT description,locale FROM categoriesdescription WHERE category = (?)', (category,))
        description_data = self.cursor.fetchall()
        for description,locale in description_data:
            data[locale] = description
        return data

    def retrieveLicensedata(self, idpackage):

        # insert license information
        if not self.doesTableExist("licensedata"):
            return {}
        licenses = self.retrieveLicense(idpackage)
        licenses = licenses.split()
        licdata = {}
        for licname in licenses:
            licname = licname.strip()
            if not self.entropyTools.is_valid_string(licname):
                continue

            self.connection.text_factory = lambda x: unicode(x, "raw_unicode_escape")

            self.cursor.execute('SELECT text FROM licensedata WHERE licensename = (?)', (licname,))
            lictext = self.cursor.fetchone()
            if lictext != None:
                licdata[licname] = str(lictext[0])

        return licdata

    def retrieveLicensedataKeys(self, idpackage):

        if not self.doesTableExist("licensedata"):
            return set()
        licenses = self.retrieveLicense(idpackage)
        licenses = licenses.split()
        licdata = set()
        for licname in licenses:
            licname = licname.strip()
            if not self.entropyTools.is_valid_string(licname):
                continue
            self.cursor.execute('SELECT licensename FROM licensedata WHERE licensename = (?)', (licname,))
            licidentifier = self.cursor.fetchone()
            if licidentifier:
                licdata.add(licidentifier[0])

        return licdata

    def retrieveLicenseText(self, license_name):

        if not self.doesTableExist("licensedata"):
            return None

        self.connection.text_factory = lambda x: unicode(x, "raw_unicode_escape")

        self.cursor.execute('SELECT text FROM licensedata WHERE licensename = (?)', (license_name,))
        text = self.cursor.fetchone()
        if not text:
            return None
        return str(text[0])

    def retrieveLicense(self, idpackage):
        self.cursor.execute('SELECT license FROM baseinfo,licenses WHERE baseinfo.idpackage = (?) and baseinfo.idlicense = licenses.idlicense', (idpackage,))
        licname = self.cursor.fetchone()
        if licname:
            return licname[0]

    def retrieveCompileFlags(self, idpackage):
        self.cursor.execute('SELECT chost,cflags,cxxflags FROM flags,extrainfo WHERE extrainfo.idpackage = (?) and extrainfo.idflags = flags.idflags', (idpackage,))
        flags = self.cursor.fetchone()
        if not flags:
            flags = ("N/A","N/A","N/A")
        return flags

    def retrieveDepends(self, idpackage, atoms = False, key_slot = False):

        # sanity check on the table
        if not self.isDependsTableSane(): # is empty, need generation
            self.regenerateDependsTable(output = False)

        if atoms:
            self.cursor.execute('SELECT baseinfo.atom FROM dependstable,dependencies,baseinfo WHERE dependstable.idpackage = (?) and dependstable.iddependency = dependencies.iddependency and baseinfo.idpackage = dependencies.idpackage', (idpackage,))
            result = self.fetchall2set(self.cursor.fetchall())
        elif key_slot:
            self.cursor.execute('SELECT categories.category || "/" || baseinfo.name,baseinfo.slot FROM baseinfo,categories,dependstable,dependencies WHERE dependstable.idpackage = (?) and dependstable.iddependency = dependencies.iddependency and baseinfo.idpackage = dependencies.idpackage and categories.idcategory = baseinfo.idcategory', (idpackage,))
            result = self.cursor.fetchall()
        else:
            self.cursor.execute('SELECT dependencies.idpackage FROM dependstable,dependencies WHERE dependstable.idpackage = (?) and dependstable.iddependency = dependencies.iddependency', (idpackage,))
            result = self.fetchall2set(self.cursor.fetchall())

        return result

    # You must provide the full atom to this function
    # WARNING: this function does not support branches
    # NOTE: server side uses this regardless branch specification because it already handles it in updatePackage()
    def isPackageAvailable(self,pkgatom):
        pkgatom = self.entropyTools.removePackageOperators(pkgatom)
        self.cursor.execute('SELECT idpackage FROM baseinfo WHERE atom = (?)', (pkgatom,))
        result = self.cursor.fetchone()
        if result:
            return result[0]
        return -1

    def isIDPackageAvailable(self,idpackage):
        self.cursor.execute('SELECT idpackage FROM baseinfo WHERE idpackage = (?)', (idpackage,))
        result = self.cursor.fetchone()
        if not result:
            return False
        return True

    # This version is more specific and supports branches
    def isSpecificPackageAvailable(self, pkgkey, branch, branch_operator = "="):
        pkgkey = self.entropyTools.removePackageOperators(pkgkey)
        self.cursor.execute('SELECT idpackage FROM baseinfo WHERE atom = (?) AND branch '+branch_operator+' (?)', (pkgkey,branch,))
        result = self.cursor.fetchone()
        if not result:
            return False
        return True

    def isCategoryAvailable(self,category):
        self.cursor.execute('SELECT idcategory FROM categories WHERE category = (?)', (category,))
        result = self.cursor.fetchone()
        if not result:
            return -1
        return result[0]

    def isProtectAvailable(self,protect):
        self.cursor.execute('SELECT idprotect FROM configprotectreference WHERE protect = (?)', (protect,))
        result = self.cursor.fetchone()
        if not result:
            return -1
        return result[0]

    def isFileAvailable(self, myfile, get_id = False):
        self.cursor.execute('SELECT idpackage FROM content WHERE file = (?)', (myfile,))
        result = self.cursor.fetchall()
        if get_id:
            return self.fetchall2set(result)
        elif result:
            return True
        return False

    def resolveNeeded(self, needed, elfclass = -1):

        cache = self.fetchSearchCache(needed,'resolveNeeded')
        if cache != None: return cache

        ldpaths = self.entropyTools.collectLinkerPaths()
        mypaths = [os.path.join(x,needed) for x in ldpaths]

        query = """
        SELECT
                idpackage,file
        FROM
                content
        WHERE
                content.file IN (%s)
        """ % ( ('?,'*len(mypaths))[:-1], )

        self.cursor.execute(query,mypaths)
        results = self.cursor.fetchall()

        if elfclass == -1:
            mydata = set(results)
        else:
            mydata = set()
            for data in results:
                if not os.access(data[1],os.R_OK):
                    continue
                myclass = self.entropyTools.read_elf_class(data[1])
                if myclass == elfclass:
                    mydata.add(data)

        self.storeSearchCache(needed,'resolveNeeded',mydata)
        return mydata

    def isSourceAvailable(self,source):
        self.cursor.execute('SELECT idsource FROM sourcesreference WHERE source = (?)', (source,))
        result = self.cursor.fetchone()
        if not result:
            return -1
        return result[0]

    def isDependencyAvailable(self,dependency):
        self.cursor.execute('SELECT iddependency FROM dependenciesreference WHERE dependency = (?)', (dependency,))
        result = self.cursor.fetchone()
        if not result:
            return -1
        return result[0]

    def isKeywordAvailable(self,keyword):
        self.cursor.execute('SELECT idkeyword FROM keywordsreference WHERE keywordname = (?)', (keyword,))
        result = self.cursor.fetchone()
        if not result:
            return -1
        return result[0]

    def isUseflagAvailable(self,useflag):
        self.cursor.execute('SELECT idflag FROM useflagsreference WHERE flagname = (?)', (useflag,))
        result = self.cursor.fetchone()
        if not result:
            return -1
        return result[0]

    def isEclassAvailable(self,eclass):
        self.cursor.execute('SELECT idclass FROM eclassesreference WHERE classname = (?)', (eclass,))
        result = self.cursor.fetchone()
        if not result:
            return -1
        return result[0]

    def isNeededAvailable(self,needed):
        self.cursor.execute('SELECT idneeded FROM neededreference WHERE library = (?)', (needed,))
        result = self.cursor.fetchone()
        if not result:
            return -1
        return result[0]

    def isCounterAvailable(self, counter, branch = None, branch_operator = "="):
        result = False
        if not branch:
            branch = etpConst['branch']
        self.cursor.execute('SELECT counter FROM counters WHERE counter = (?) and branch '+branch_operator+' (?)', (counter,branch,))
        result = self.cursor.fetchone()
        if result:
            result = True
        return result

    def isCounterTrashed(self, counter):
        self.cursor.execute('SELECT counter FROM trashedcounters WHERE counter = (?)', (counter,))
        result = self.cursor.fetchone()
        if result:
            return True
        return False

    def getIDPackageFromCounter(self, counter, branch = None, branch_operator = "="):
        if not branch:
            branch = etpConst['branch']
        self.cursor.execute('SELECT idpackage FROM counters WHERE counter = (?) and branch '+branch_operator+' (?)', (counter,branch,))
        result = self.cursor.fetchone()
        if not result:
            return 0
        return result[0]

    def isLicensedataKeyAvailable(self, license_name):
        if not self.doesTableExist("licensedata"):
            return True
        self.cursor.execute('SELECT licensename FROM licensedata WHERE licensename = (?)', (license_name,))
        result = self.cursor.fetchone()
        if not result:
            return False
        return True

    def isLicenseAccepted(self, license_name):
        self.cursor.execute('SELECT licensename FROM licenses_accepted WHERE licensename = (?)', (license_name,))
        result = self.cursor.fetchone()
        if not result:
            return False
        return True

    def acceptLicense(self, license_name):
        if self.readOnly or (not self.entropyTools.is_user_in_entropy_group()):
            return
        if self.isLicenseAccepted(license_name):
            return
        self.cursor.execute('INSERT INTO licenses_accepted VALUES (?)', (license_name,))
        self.commitChanges()

    def isLicenseAvailable(self,pkglicense):
        if not self.entropyTools.is_valid_string(pkglicense):
            pkglicense = ' '
        self.cursor.execute('SELECT idlicense FROM licenses WHERE license = (?)', (pkglicense,))
        result = self.cursor.fetchone()
        if not result:
            return -1
        return result[0]

    def isSystemPackage(self,idpackage):
        self.cursor.execute('SELECT idpackage FROM systempackages WHERE idpackage = (?)', (idpackage,))
        result = self.cursor.fetchone()
        if result:
            return True
        return False

    def isInjected(self,idpackage):
        try:
            self.cursor.execute('SELECT idpackage FROM injected WHERE idpackage = (?)', (idpackage,))
        except:
            # readonly database?
            return False
        result = self.cursor.fetchone()
        rslt = False
        if result:
            rslt = True
        return rslt

    def areCompileFlagsAvailable(self,chost,cflags,cxxflags):

        self.cursor.execute('SELECT idflags FROM flags WHERE chost = (?) AND cflags = (?) AND cxxflags = (?)', 
            (chost,cflags,cxxflags,)
        )
        result = self.cursor.fetchone()
        if not result:
            return -1
        return result[0]

    def searchBelongs(self, file, like = False, branch = None, branch_operator = "="):

        branchstring = ''
        searchkeywords = [file]
        if branch:
            searchkeywords.append(branch)
            branchstring = ' and baseinfo.branch '+branch_operator+' (?)'

        if like:
            self.cursor.execute('SELECT content.idpackage FROM content,baseinfo WHERE file LIKE (?) and content.idpackage = baseinfo.idpackage '+branchstring, searchkeywords)
        else:
            self.cursor.execute('SELECT content.idpackage FROM content,baseinfo WHERE file = (?) and content.idpackage = baseinfo.idpackage '+branchstring, searchkeywords)

        return self.fetchall2set(self.cursor.fetchall())

    ''' search packages that uses the eclass provided '''
    def searchEclassedPackages(self, eclass, atoms = False): # atoms = return atoms directly
        if atoms:
            self.cursor.execute('SELECT baseinfo.atom,eclasses.idpackage FROM baseinfo,eclasses,eclassesreference WHERE eclassesreference.classname = (?) and eclassesreference.idclass = eclasses.idclass and eclasses.idpackage = baseinfo.idpackage', (eclass,))
            return self.cursor.fetchall()
        else:
            self.cursor.execute('SELECT idpackage FROM baseinfo WHERE versiontag = (?)', (eclass,))
            return self.fetchall2set(self.cursor.fetchall())

    ''' search packages whose versiontag matches the one provided '''
    def searchTaggedPackages(self, tag, atoms = False): # atoms = return atoms directly
        if atoms:
            self.cursor.execute('SELECT atom,idpackage FROM baseinfo WHERE versiontag = (?)', (tag,))
            return self.cursor.fetchall()
        else:
            self.cursor.execute('SELECT idpackage FROM baseinfo WHERE versiontag = (?)', (tag,))
            return self.fetchall2set(self.cursor.fetchall())

    def searchLicenses(self, mylicense, caseSensitive = False, atoms = False):

        if not self.entropyTools.is_valid_string(mylicense):
            return []

        request = "baseinfo.idpackage"
        if atoms:
            request = "baseinfo.atom,baseinfo.idpackage"

        if caseSensitive:
            self.cursor.execute('SELECT '+request+' FROM baseinfo,licenses WHERE licenses.license LIKE (?) and licenses.idlicense = baseinfo.idlicense', ("%"+mylicense+"%",))
        else:
            self.cursor.execute('SELECT '+request+' FROM baseinfo,licenses WHERE LOWER(licenses.license) LIKE (?) and licenses.idlicense = baseinfo.idlicense', ("%"+mylicense+"%".lower(),))
        if atoms:
            return self.cursor.fetchall()
        return self.fetchall2set(self.cursor.fetchall())

    ''' search packages whose slot matches the one provided '''
    def searchSlottedPackages(self, slot, atoms = False): # atoms = return atoms directly
        if atoms:
            self.cursor.execute('SELECT atom,idpackage FROM baseinfo WHERE slot = (?)', (slot,))
            return self.cursor.fetchall()
        else:
            self.cursor.execute('SELECT idpackage FROM baseinfo WHERE slot = (?)', (slot,))
            return self.fetchall2set(self.cursor.fetchall())

    def searchKeySlot(self, key, slot, branch = None):

        branchstring = ''
        params = [key,slot]
        if branch:
            params.append(branch)
            branchstring = ' and baseinfo.branch = (?)'

        self.cursor.execute('SELECT idpackage FROM baseinfo,categories WHERE categories.category || "/" || baseinfo.name = (?) and baseinfo.slot = (?) and baseinfo.idcategory = categories.idcategory'+branchstring, params)
        data = self.cursor.fetchall()

        return data

    ''' search packages that need the specified library (in neededreference table) specified by keyword '''
    def searchNeeded(self, keyword, like = False):
        if like:
            self.cursor.execute('SELECT needed.idpackage FROM needed,neededreference WHERE library LIKE (?) and needed.idneeded = neededreference.idneeded', (keyword,))
        else:
            self.cursor.execute('SELECT needed.idpackage FROM needed,neededreference WHERE library = (?) and needed.idneeded = neededreference.idneeded', (keyword,))
	return self.fetchall2set(self.cursor.fetchall())

    # FIXME: deprecate and add functionalities to the function above
    ''' same as above but with branch support '''
    def searchNeededInBranch(self, keyword, branch):
	self.cursor.execute('SELECT needed.idpackage FROM needed,neededreference,baseinfo WHERE library = (?) and needed.idneeded = neededreference.idneeded and baseinfo.branch = (?)', (keyword,branch,))
	return self.fetchall2set(self.cursor.fetchall())

    ''' search dependency string inside dependenciesreference table and retrieve iddependency '''
    def searchDependency(self, dep, like = False, multi = False, strings = False):
        sign = "="
        if like:
            sign = "LIKE"
            dep = "%"+dep+"%"
        item = 'iddependency'
        if strings:
            item = 'dependency'
        self.cursor.execute('SELECT '+item+' FROM dependenciesreference WHERE dependency '+sign+' (?)', (dep,))
        if multi:
            return self.fetchall2set(self.cursor.fetchall())
        else:
            iddep = self.cursor.fetchone()
            if iddep:
                iddep = iddep[0]
            else:
                iddep = -1
            return iddep

    ''' search iddependency inside dependencies table and retrieve idpackages '''
    def searchIdpackageFromIddependency(self, iddep):
        self.cursor.execute('SELECT idpackage FROM dependencies WHERE iddependency = (?)', (iddep,))
        return self.fetchall2set(self.cursor.fetchall())

    def searchPackages(self, keyword, sensitive = False, slot = None, tag = None, branch = None):

        searchkeywords = ["%"+keyword+"%"]
        slotstring = ''
        if slot:
            searchkeywords.append(slot)
            slotstring = ' and slot = (?)'
        tagstring = ''
        if tag:
            searchkeywords.append(tag)
            tagstring = ' and versiontag = (?)'
        branchstring = ''
        if branch:
            searchkeywords.append(branch)
            branchstring = ' and branch = (?)'

        if (sensitive):
            self.cursor.execute('SELECT atom,idpackage,branch FROM baseinfo WHERE atom LIKE (?)'+slotstring+tagstring+branchstring, searchkeywords)
        else:
            self.cursor.execute('SELECT atom,idpackage,branch FROM baseinfo WHERE LOWER(atom) LIKE (?)'+slotstring+tagstring+branchstring, searchkeywords)
        return self.cursor.fetchall()

    def searchProvide(self, keyword, slot = None, tag = None, branch = None, justid = False):

        slotstring = ''
        searchkeywords = [keyword]
        if slot:
            searchkeywords.append(slot)
            slotstring = ' and baseinfo.slot = (?)'
        tagstring = ''
        if tag:
            searchkeywords.append(tag)
            tagstring = ' and baseinfo.versiontag = (?)'
        branchstring = ''
        if branch:
            searchkeywords.append(branch)
            branchstring = ' and baseinfo.branch = (?)'
        atomstring = ''
        if not justid:
            atomstring = 'baseinfo.atom,'

        self.cursor.execute('SELECT '+atomstring+'baseinfo.idpackage FROM baseinfo,provide WHERE provide.atom = (?) and provide.idpackage = baseinfo.idpackage'+slotstring+tagstring+branchstring, searchkeywords)

        if justid:
            results = self.fetchall2list(self.cursor.fetchall())
        else:
            results = self.cursor.fetchall()
        return results

    def searchPackagesByDescription(self, keyword):
        self.cursor.execute('SELECT baseinfo.atom,baseinfo.idpackage FROM extrainfo,baseinfo WHERE LOWER(extrainfo.description) LIKE (?) and baseinfo.idpackage = extrainfo.idpackage', ("%"+keyword.lower()+"%",))
        return self.cursor.fetchall()

    def searchPackagesByName(self, keyword, sensitive = False, branch = None, justid = False):

        if sensitive:
            searchkeywords = [keyword]
        else:
            searchkeywords = [keyword.lower()]
        branchstring = ''
        atomstring = ''
        if not justid:
            atomstring = 'atom,'
        if branch:
            searchkeywords.append(branch)
            branchstring = ' and branch = (?)'

        if sensitive:
            self.cursor.execute('SELECT '+atomstring+'idpackage FROM baseinfo WHERE name = (?)'+branchstring, searchkeywords)
        else:
            self.cursor.execute('SELECT '+atomstring+'idpackage FROM baseinfo WHERE LOWER(name) = (?)'+branchstring, searchkeywords)

        if justid:
            results = self.fetchall2list(self.cursor.fetchall())
        else:
            results = self.cursor.fetchall()
        return results


    def searchPackagesByCategory(self, keyword, like = False, branch = None):

        searchkeywords = [keyword]
        branchstring = ''
        if branch:
            searchkeywords.append(branch)
            branchstring = ' and branch = (?)'

        if like:
            self.cursor.execute('SELECT baseinfo.atom,baseinfo.idpackage FROM baseinfo,categories WHERE categories.category LIKE (?) and baseinfo.idcategory = categories.idcategory '+branchstring, searchkeywords)
        else:
            self.cursor.execute('SELECT baseinfo.atom,baseinfo.idpackage FROM baseinfo,categories WHERE categories.category = (?) and baseinfo.idcategory = categories.idcategory '+branchstring, searchkeywords)

        results = self.cursor.fetchall()

        return results

    def searchPackagesByNameAndCategory(self, name, category, sensitive = False, branch = None, justid = False):

        myname = name
        mycat = category
        if not sensitive:
            myname = name.lower()
            mycat = category.lower()

        searchkeywords = [myname,mycat]
        branchstring = ''
        if branch:
            searchkeywords.append(branch)
            branchstring = ' and branch = (?)'
        atomstring = ''
        if not justid:
            atomstring = 'atom,'

        if sensitive:
            self.cursor.execute('SELECT '+atomstring+'idpackage FROM baseinfo WHERE name = (?) AND idcategory IN (SELECT idcategory FROM categories WHERE category = (?))'+branchstring, searchkeywords)
        else:
            self.cursor.execute('SELECT '+atomstring+'idpackage FROM baseinfo WHERE LOWER(name) = (?) AND idcategory IN (SELECT idcategory FROM categories WHERE LOWER(category) = (?))'+branchstring, searchkeywords)
            ''

        if justid:
            results = self.fetchall2list(self.cursor.fetchall())
        else:
            results = self.cursor.fetchall()
        return results

    def searchPackagesKeyVersion(self, key, version, branch = None, sensitive = False):

        searchkeywords = []
        if sensitive:
            searchkeywords.append(key)
        else:
            searchkeywords.append(key.lower())

        searchkeywords.append(version)

        branchstring = ''
        if branch:
            searchkeywords.append(branch)
            branchstring = ' and branch = (?)'

        if (sensitive):
            self.cursor.execute('SELECT baseinfo.atom,baseinfo.idpackage FROM baseinfo,categories WHERE categories.category || "/" || baseinfo.name = (?) and version = (?) and baseinfo.idcategory = categories.idcategory '+branchstring, searchkeywords)
        else:
            self.cursor.execute('SELECT baseinfo.atom,baseinfo.idpackage FROM baseinfo,categories WHERE LOWER(categories.category) || "/" || LOWER(baseinfo.name) = (?) and version = (?) and baseinfo.idcategory = categories.idcategory '+branchstring, searchkeywords)

        results = self.cursor.fetchall()

        return results

    def listAllPackages(self):
        self.cursor.execute('SELECT atom,idpackage,branch FROM baseinfo')
        return self.cursor.fetchall()

    def listAllInjectedPackages(self, justFiles = False):
        self.cursor.execute('SELECT idpackage FROM injected')
        injecteds = self.fetchall2set(self.cursor.fetchall())
        results = set()
        # get download
        for injected in injecteds:
            download = self.retrieveDownloadURL(injected)
            if justFiles:
                results.add(download)
            else:
                results.add((download,injected))
        return results

    def listAllCounters(self, onlycounters = False, branch = None, branch_operator = "="):

        branchstring = ''
        if branch:
            branchstring = ' WHERE branch '+branch_operator+' "'+str(branch)+'"'
        if onlycounters:
            self.cursor.execute('SELECT counter FROM counters'+branchstring)
            return self.fetchall2set(self.cursor.fetchall())
        else:
            self.cursor.execute('SELECT counter,idpackage FROM counters'+branchstring)
            return self.cursor.fetchall()

    def listAllIdpackages(self, branch = None, branch_operator = "=", order_by = None):

        branchstring = ''
        orderbystring = ''
        searchkeywords = []
        if branch:
            searchkeywords.append(branch)
            branchstring = ' where branch %s (?)' % (str(branch_operator),)
        if order_by:
            orderbystring = ' order by '+order_by

        self.cursor.execute('SELECT idpackage FROM baseinfo'+branchstring+orderbystring, searchkeywords)

        if order_by:
            results = self.fetchall2list(self.cursor.fetchall())
        else:
            results = self.fetchall2set(self.cursor.fetchall())
        return results

    def listAllDependencies(self, only_deps = False):
        if only_deps:
            self.cursor.execute('SELECT dependency FROM dependenciesreference')
            return self.fetchall2set(self.cursor.fetchall())
        else:
            self.cursor.execute('SELECT * FROM dependenciesreference')
            return self.cursor.fetchall()

    def listAllBranches(self):

        cache = self.live_cache.get('listAllBranches')
        if cache != None:
            return cache

        self.cursor.execute('SELECT distinct branch FROM baseinfo')
        results = self.fetchall2set(self.cursor.fetchall())

        self.live_cache['listAllBranches'] = results.copy()
        return results

    def listIdPackagesInIdcategory(self,idcategory):
        self.cursor.execute('SELECT idpackage FROM baseinfo where idcategory = (?)', (idcategory,))
        return self.fetchall2set(self.cursor.fetchall())

    def listIdpackageDependencies(self, idpackage):
        self.cursor.execute('SELECT dependenciesreference.iddependency,dependenciesreference.dependency FROM dependenciesreference,dependencies WHERE dependencies.idpackage = (?) AND dependenciesreference.iddependency = dependencies.iddependency', (idpackage,))
        return set(self.cursor.fetchall())

    def listBranchPackagesTbz2(self, branch, do_sort = True):
        self.cursor.execute('SELECT extrainfo.download FROM baseinfo,extrainfo WHERE baseinfo.branch = (?) AND baseinfo.idpackage = extrainfo.idpackage', (branch,))
        result = self.fetchall2set(self.cursor.fetchall())
        sorted_result = []
        for package in result:
            if package:
                sorted_result.append(os.path.basename(package))
        if do_sort:
            sorted_result.sort()
        return sorted_result

    def listBranchPackages(self, branch):
        self.cursor.execute('SELECT atom,idpackage FROM baseinfo WHERE branch = (?)', (branch,))
        return self.cursor.fetchall()

    def listAllFiles(self, clean = False):
        self.cursor.execute('SELECT file FROM content')
        if clean:
            return self.fetchall2set(self.cursor.fetchall())
        else:
            return self.fetchall2list(self.cursor.fetchall())

    def listAllCategories(self):
        self.cursor.execute('SELECT idcategory,category FROM categories')
        return self.cursor.fetchall()

    def listConfigProtectDirectories(self, mask = False):
        query = 'SELECT max(idprotect) FROM configprotect'
        if mask:
            query += 'mask'
        self.cursor.execute(query)
        r = self.cursor.fetchone()
        if not r:
            return []

        mymax = r[0]
        self.cursor.execute('SELECT protect FROM configprotectreference where idprotect >= (?) and idprotect <= (?) order by protect', (1,mymax,))
        results = self.cursor.fetchall()
        dirs = []
        for row in results:
            mydirs = row[0].split()
            for x in mydirs:
                if x not in dirs:
                    dirs.append(x)
        return dirs

    def switchBranch(self, idpackage, tobranch):
        self.checkReadOnly()

        mycat = self.retrieveCategory(idpackage)
        myname = self.retrieveName(idpackage)
        myslot = self.retrieveSlot(idpackage)
        mybranch = self.retrieveBranch(idpackage)
        mydownload = self.retrieveDownloadURL(idpackage)
        import re
        out = re.subn('/'+mybranch+'/','/'+tobranch+'/',mydownload)
        newdownload = out[0]

        # remove package with the same key+slot and tobranch if exists
        match = self.atomMatch(mycat+"/"+myname, matchSlot = myslot, matchBranches = (tobranch,))
        if match[0] != -1:
            self.removePackage(match[0])

        # now switch selected idpackage to the new branch
        self.cursor.execute('UPDATE baseinfo SET branch = (?) WHERE idpackage = (?)', (tobranch,idpackage,))
        self.cursor.execute('UPDATE extrainfo SET download = (?) WHERE idpackage = (?)', (newdownload,idpackage,))
        self.commitChanges()

    def databaseStructureUpdates(self):

        if not self.doesTableExist("licensedata"):
            self.createLicensedataTable()

        if not self.doesTableExist("licenses_accepted") and (self.dbname == etpConst['clientdbid']):
            self.createLicensesAcceptedTable()

        if not self.doesColumnInTableExist("baseinfo","trigger"):
            self.createTriggerColumn()

        if not self.doesTableExist("counters"):
            self.createCountersTable()
        elif not self.doesColumnInTableExist("counters","branch"):
            self.createCountersBranchColumn()

        if not self.doesTableExist("trashedcounters"):
            self.createTrashedcountersTable()

        if not self.doesTableExist("sizes"):
            self.createSizesTable()

        if not self.doesTableExist("triggers"):
            self.createTriggerTable()

        if not self.doesTableExist("messages"):
            self.createMessagesTable()

        if not self.doesTableExist("injected"):
            self.createInjectedTable()

        if not self.doesTableExist("systempackages"):
            self.createSystemPackagesTable()

        if (not self.doesTableExist("configprotect")) or (not self.doesTableExist("configprotectreference")):
            self.createProtectTable()

        if not self.doesColumnInTableExist("content","type"):
            self.createContentTypeColumn()

        if not self.doesTableExist("eclasses"):
            self.createEclassesTable()

        if not self.doesTableExist("treeupdates"):
            self.createTreeupdatesTable()

        if not self.doesTableExist("treeupdatesactions"):
            self.createTreeupdatesactionsTable()
        elif not self.doesColumnInTableExist("treeupdatesactions","branch"):
            self.createTreeupdatesactionsBranchColumn()
        elif not self.doesColumnInTableExist("treeupdatesactions","date"):
            self.createTreeupdatesactionsDateColumn()

        if not self.doesTableExist("needed"):
            self.createNeededTable()
        elif not self.doesColumnInTableExist("needed","elfclass"):
            self.createNeededElfclassColumn()

        if not self.doesTableExist("installedtable") and (self.dbname == etpConst['clientdbid']):
            self.createInstalledTable()

        if not self.doesTableExist("entropy_misc_counters"):
            self.createEntropyMiscCountersTable()

        if not self.doesColumnInTableExist("dependencies","type"):
            self.createDependenciesTypeColumn()

        if not self.doesTableExist("categoriesdescription"):
            self.createCategoriesdescriptionTable()

        # these are the tables moved to INTEGER PRIMARY KEY AUTOINCREMENT
        autoincrement_tables = [
            'treeupdatesactions',
            'neededreference',
            'eclassesreference',
            'configprotectreference',
            'flags',
            'licenses',
            'categories',
            'keywordsreference',
            'useflagsreference',
            'sourcesreference',
            'dependenciesreference',
            'baseinfo'
        ]
        autoinc = False
        for table in autoincrement_tables:
            x = self.migrateTableToAutoincrement(table)
            if x: autoinc = True
        if autoinc:
            self.updateProgress(
                                            red("Entropy database: regenerating indexes after migration."),
                                            importance = 1,
                                            type = "warning",
                                            header = blue(" !!! ")
                            )
            self.createAllIndexes()

        # do manual atoms update
        if os.access(self.dbFile,os.W_OK) and \
            (self.dbname != etpConst['genericdbid']):
                old_readonly = self.readOnly
                self.readOnly = False
                self.fixKdeDepStrings()
                self.readOnly = old_readonly

        self.connection.commit()

    def migrateTableToAutoincrement(self, table):

        self.cursor.execute('select sql from sqlite_master where type = (?) and name = (?);', ("table",table))
        schema = self.cursor.fetchone()
        if not schema:
            return False
        schema = schema[0]
        if schema.find("AUTOINCREMENT") != -1:
            return False
        schema = schema.replace('PRIMARY KEY','PRIMARY KEY AUTOINCREMENT')
        new_schema = schema
        totable = table+"_autoincrement"
        schema = schema.replace('CREATE TABLE '+table,'CREATE TEMPORARY TABLE '+totable)
        self.updateProgress(
                                        red("Entropy database: migrating table ")+blue(table),
                                        importance = 1,
                                        type = "warning",
                                        header = blue(" !!! ")
                        )
        # create table
        self.cursor.execute('DROP TABLE IF EXISTS '+totable)
        self.cursor.execute(schema)
        columns = ','.join(self.getColumnsInTable(table))

        temp_query = 'INSERT INTO '+totable+' SELECT '+columns+' FROM '+table
        self.cursor.execute(temp_query)

        self.cursor.execute('DROP TABLE '+table)
        self.cursor.execute(new_schema)

        temp_query = 'INSERT INTO '+table+' SELECT '+columns+' FROM '+totable
        self.cursor.execute(temp_query)

        self.cursor.execute('DROP TABLE '+totable)
        self.commitChanges()
        return True

    def fixKdeDepStrings(self):

        # check if we need to do it
        cur_id = self.getForcedAtomsUpdateId()
        if cur_id >= etpConst['misc_counters']['forced_atoms_update_ids']['kde']:
            return

        self.updateProgress(
            red("Entropy database: fixing KDE dep strings on %s. Please wait..." % (self.dbname,)),
            importance = 1,
            type = "warning",
            header = blue(" !!! ")
        )

        # uhu, let's roooock
        search_deps = {
            ">=kde-base/kdelibs-3.0": 'kde-base/kdelibs:3.5',
            ">=kde-base/kdelibs-3.1": 'kde-base/kdelibs:3.5',
            ">=kde-base/kdelibs-3.2": 'kde-base/kdelibs:3.5',
            ">=kde-base/kdelibs-3.3": 'kde-base/kdelibs:3.5',
            ">=kde-base/kdelibs-3.4": 'kde-base/kdelibs:3.5',
            ">=kde-base/kdelibs-3.5": 'kde-base/kdelibs:3.5',
            ">=kde-base/kdelibs-3": 'kde-base/kdelibs:3.5',
            ">=kde-base/kdelibs-3.0": 'kde-base/kdelibs:3.5',
            ">=kde-base/kdelibs-3.0.0": 'kde-base/kdelibs:3.5',
            ">=kde-base/kdelibs-3.0.5": 'kde-base/kdelibs:3.5',
            ">=kde-base/kdelibs-3.1": 'kde-base/kdelibs:3.5',
            ">=kde-base/kdelibs-3.1.0": 'kde-base/kdelibs:3.5',

        }
        self.cursor.execute('select iddependency,dependency from dependenciesreference')
        depdata = self.cursor.fetchall()
        for iddepedency, depstring in depdata:
            if depstring in search_deps:
                self.setDependency(iddepedency, search_deps[depstring])

        # regenerate depends
        while 1: # avoid users interruption
            self.regenerateDependsTable()
            break

        self.setForcedAtomsUpdateId(etpConst['misc_counters']['forced_atoms_update_ids']['kde'])
        self.commitChanges()
        # drop all cache
        self.clearCache(depends = True)


    def getForcedAtomsUpdateId(self):
        self.cursor.execute(
            'SELECT counter FROM entropy_misc_counters WHERE idtype = (?)',
            (etpConst['misc_counters']['forced_atoms_update_ids']['__idtype__'],)
        )
        myid = self.cursor.fetchone()
        if not myid:
            return self.setForcedAtomsUpdateId(0)
        return myid[0]

    def setForcedAtomsUpdateId(self, myid):
        self.cursor.execute(
            'DELETE FROM entropy_misc_counters WHERE idtype = (?)',
            (etpConst['misc_counters']['forced_atoms_update_ids']['__idtype__'],)
        )
        self.cursor.execute(
            'INSERT INTO entropy_misc_counters VALUES (?,?)',
            (etpConst['misc_counters']['forced_atoms_update_ids']['__idtype__'],myid)
        )
        return myid

    def validateDatabase(self):
        self.cursor.execute('select name from SQLITE_MASTER where type = (?) and name = (?)', ("table","baseinfo"))
        rslt = self.cursor.fetchone()
        if rslt == None:
            raise exceptionTools.SystemDatabaseError("SystemDatabaseError: table baseinfo not found. Either does not exist or corrupted.")
        self.cursor.execute('select name from SQLITE_MASTER where type = (?) and name = (?)', ("table","extrainfo"))
        rslt = self.cursor.fetchone()
        if rslt == None:
            raise exceptionTools.SystemDatabaseError("SystemDatabaseError: table extrainfo not found. Either does not exist or corrupted.")

    def checkDatabaseApi(self):

        dbapi = self.getApi()
        if dbapi > etpConst['etpapi']:
            self.updateProgress(
                                            red("Repository EAPI > Entropy EAPI. Please update Equo/Entropy as soon as possible !"),
                                            importance = 1,
                                            type = "warning",
                                            header = " * ! * ! * ! * "
                            )

    def doDatabaseImport(self, dumpfile, dbfile):
        import subprocess
        sqlite3_exec = "/usr/bin/sqlite3 %s < %s" % (dbfile,dumpfile,)
        retcode = subprocess.call(sqlite3_exec, shell = True)
        return retcode

    def doDatabaseExport(self, dumpfile):

        dumpfile.write("BEGIN TRANSACTION;\n")
        self.cursor.execute("SELECT name, type, sql FROM sqlite_master WHERE sql NOT NULL AND type=='table'")
        for name, x, sql in self.cursor.fetchall():

            self.updateProgress(
                                            red("Exporting database table ")+"["+blue(str(name))+"]",
                                            importance = 0,
                                            type = "info",
                                            back = True,
                                            header = "   "
                            )

            if name == "sqlite_sequence":
                dumpfile.write("DELETE FROM sqlite_sequence;\n")
            elif name == "sqlite_stat1":
                dumpfile.write("ANALYZE sqlite_master;\n")
            elif name.startswith("sqlite_"):
                continue
            else:
                dumpfile.write("%s;\n" % sql)

            self.cursor.execute("PRAGMA table_info('%s')" % name)
            cols = [str(r[1]) for r in self.cursor.fetchall()]
            q = "SELECT 'INSERT INTO \"%(tbl_name)s\" VALUES("
            q += ", ".join(["'||quote(" + x + ")||'" for x in cols])
            q += ")' FROM '%(tbl_name)s'"
            self.cursor.execute(q % {'tbl_name': name})
            self.connection.text_factory = lambda x: unicode(x, "raw_unicode_escape")
            for row in self.cursor:
                dumpfile.write("%s;\n" % str(row[0].encode('raw_unicode_escape')))

        self.cursor.execute("SELECT name, type, sql FROM sqlite_master WHERE sql NOT NULL AND type!='table' AND type!='meta'")
        for name, x, sql in self.cursor.fetchall():
            dumpfile.write("%s;\n" % sql)

        dumpfile.write("COMMIT;\n")
        try:
            dumpfile.flush()
        except:
            pass
        self.updateProgress(
                                        red("Database Export completed."),
                                        importance = 0,
                                        type = "info",
                                        header = "   "
                        )
        # remember to close the file


    # FIXME: this is only compatible with SQLITE
    def doesTableExist(self, table):
        self.cursor.execute('select name from SQLITE_MASTER where type = (?) and name = (?)', ("table",table))
        rslt = self.cursor.fetchone()
        if rslt == None:
            return False
        return True

    # FIXME: this is only compatible with SQLITE
    def doesColumnInTableExist(self, table, column):
        self.cursor.execute('PRAGMA table_info( '+table+' )')
        rslt = self.cursor.fetchall()
        if not rslt:
            return False
        found = False
        for row in rslt:
            if row[1] == column:
                found = True
                break
        return found

    # FIXME: this is only compatible with SQLITE
    def getColumnsInTable(self, table):
        self.cursor.execute('PRAGMA table_info( '+table+' )')
        rslt = self.cursor.fetchall()
        columns = []
        for row in rslt:
            columns.append(row[1])
        return columns

    def database_checksum(self):
        # primary keys are now autoincrement
        self.cursor.execute('select idpackage from baseinfo')
        c_hash = hash(tuple(self.cursor.fetchall()))
        return str(c_hash)


########################################################
####
##   Client Database API / but also used by server part
#

    def addPackageToInstalledTable(self, idpackage, repositoryName):
        self.checkReadOnly()
        self.cursor.execute(
                'INSERT into installedtable VALUES '
                '(?,?)'
                , (	idpackage,
                        repositoryName,
                        )
        )
        self.commitChanges()

    def retrievePackageFromInstalledTable(self, idpackage):
        self.checkReadOnly()
        result = 'Not available'
        try:
            self.cursor.execute('SELECT repositoryname FROM installedtable WHERE idpackage = (?)', (idpackage,))
            return self.cursor.fetchone()[0] # it's ok because it's inside try/except
        except:
            pass
        return result

    def removePackageFromInstalledTable(self, idpackage):
        self.cursor.execute('DELETE FROM installedtable WHERE idpackage = (?)', (idpackage,))

    def removePackageFromDependsTable(self, idpackage):
        try:
            self.cursor.execute('DELETE FROM dependstable WHERE idpackage = (?)', (idpackage,))
            return 0
        except:
            return 1 # need reinit

    def removeDependencyFromDependsTable(self, iddependency):
        self.checkReadOnly()
        try:
            self.cursor.execute('DELETE FROM dependstable WHERE iddependency = (?)',(iddependency,))
            self.commitChanges()
            return 0
        except:
            return 1 # need reinit

    # temporary/compat functions
    def createDependsTable(self):
        self.checkReadOnly()
        self.cursor.execute('DROP TABLE IF EXISTS dependstable;')
        self.cursor.execute('CREATE TABLE dependstable ( iddependency INTEGER PRIMARY KEY, idpackage INTEGER );')
        # this will be removed when dependstable is refilled properly
        self.cursor.execute(
                'INSERT into dependstable VALUES '
                '(?,?)'
                , (	-1,
                        -1,
                        )
        )
        if self.indexing:
            self.cursor.execute('CREATE INDEX IF NOT EXISTS dependsindex_idpackage ON dependstable ( idpackage )')
        self.commitChanges()

    def sanitizeDependsTable(self):
        self.cursor.execute('DELETE FROM dependstable where iddependency = -1')
        self.commitChanges()

    def isDependsTableSane(self):
        try:
            self.cursor.execute('SELECT iddependency FROM dependstable WHERE iddependency = -1')
        except:
            return False # table does not exist, please regenerate and re-run
        status = self.cursor.fetchone()
        if status:
            return False

        self.cursor.execute('select count(*) from dependstable')
        dependstable_count = self.cursor.fetchone()
        if dependstable_count == 0:
            return False
        return True

    def createXpakTable(self):
        self.checkReadOnly()
        self.cursor.execute('CREATE TABLE xpakdata ( idpackage INTEGER PRIMARY KEY, data BLOB );')
        self.commitChanges()

    def storeXpakMetadata(self, idpackage, blob):
        self.cursor.execute(
                'INSERT into xpakdata VALUES '
                '(?,?)', ( int(idpackage), buffer(blob), )
        )
        self.commitChanges()

    def retrieveXpakMetadata(self, idpackage):
        try:
            self.cursor.execute('SELECT data from xpakdata where idpackage = (?)', (idpackage,))
            mydata = self.cursor.fetchone()
            if not mydata:
                return ""
            else:
                return mydata[0]
        except:
            return ""
            pass

    def createCountersTable(self):
        self.cursor.execute('DROP TABLE IF EXISTS counters;')
        self.cursor.execute('CREATE TABLE counters ( counter INTEGER, idpackage INTEGER PRIMARY KEY, branch VARCHAR );')

    def CreatePackedDataTable(self):
        self.cursor.execute('CREATE TABLE packed_data ( idpack INTEGER PRIMARY KEY, data BLOB );')

    def dropAllIndexes(self):
        self.cursor.execute('SELECT name FROM SQLITE_MASTER WHERE type = "index"')
        indexes = self.fetchall2set(self.cursor.fetchall())
        for index in indexes:
            if not index.startswith("sqlite"):
                self.cursor.execute('DROP INDEX IF EXISTS %s' % (index,))

    def listAllIndexes(self, only_entropy = True):
        self.cursor.execute('SELECT name FROM SQLITE_MASTER WHERE type = "index"')
        indexes = self.fetchall2set(self.cursor.fetchall())
        if not only_entropy:
            return indexes
        myindexes = set()
        for index in indexes:
            if index.startswith("sqlite"):
                continue
            myindexes.add(index)
        return myindexes


    def createAllIndexes(self):
        self.createContentIndex()
        self.createBaseinfoIndex()
        self.createKeywordsIndex()
        self.createDependenciesIndex()
        self.createProvideIndex()
        self.createConflictsIndex()
        self.createExtrainfoIndex()
        self.createNeededIndex()
        self.createUseflagsIndex()
        self.createLicensedataIndex()
        self.createLicensesIndex()
        self.createConfigProtectReferenceIndex()
        self.createMessagesIndex()
        self.createSourcesIndex()
        self.createCountersIndex()
        self.createEclassesIndex()
        self.createCategoriesIndex()
        self.createCompileFlagsIndex()

    def createNeededIndex(self):
        if self.indexing:
            self.cursor.execute('CREATE INDEX IF NOT EXISTS neededindex ON neededreference ( library )')
            self.cursor.execute('CREATE INDEX IF NOT EXISTS neededindex_idneeded ON needed ( idneeded )')
            self.cursor.execute('CREATE INDEX IF NOT EXISTS neededindex_idpackage ON needed ( idpackage )')
            self.cursor.execute('CREATE INDEX IF NOT EXISTS neededindex_elfclass ON needed ( elfclass )')
            self.commitChanges()

    def createMessagesIndex(self):
        if self.indexing:
            self.cursor.execute('CREATE INDEX IF NOT EXISTS messagesindex ON messages ( idpackage )')
            self.commitChanges()

    def createCompileFlagsIndex(self):
        if self.indexing:
            self.cursor.execute('CREATE INDEX IF NOT EXISTS flagsindex ON flags ( chost,cflags,cxxflags )')
            self.commitChanges()

    def createUseflagsIndex(self):
        if self.indexing:
            self.cursor.execute('CREATE INDEX IF NOT EXISTS useflagsindex_useflags_idpackage ON useflags ( idpackage )')
            self.cursor.execute('CREATE INDEX IF NOT EXISTS useflagsindex_useflags_idflag ON useflags ( idflag )')
            self.cursor.execute('CREATE INDEX IF NOT EXISTS useflagsindex ON useflagsreference ( flagname )')
            self.commitChanges()

    def createContentIndex(self):
        if self.indexing:
            self.cursor.execute('CREATE INDEX IF NOT EXISTS contentindex_couple ON content ( idpackage )')
            self.cursor.execute('CREATE INDEX IF NOT EXISTS contentindex_file ON content ( file )')
            self.commitChanges()

    def createConfigProtectReferenceIndex(self):
        if self.indexing:
            self.cursor.execute('CREATE INDEX IF NOT EXISTS configprotectreferenceindex ON configprotectreference ( protect )')
            self.commitChanges()

    def createBaseinfoIndex(self):
        if self.indexing:
            self.cursor.execute('CREATE INDEX IF NOT EXISTS baseindex_atom ON baseinfo ( atom )')
            self.cursor.execute('CREATE INDEX IF NOT EXISTS baseindex_branch_name ON baseinfo ( name,branch )')
            self.cursor.execute('CREATE INDEX IF NOT EXISTS baseindex_branch_name_idcategory ON baseinfo ( name,idcategory,branch )')
            self.cursor.execute('CREATE INDEX IF NOT EXISTS baseindex_idcategory ON baseinfo ( idcategory )')
            self.commitChanges()

    def createLicensedataIndex(self):
        if self.indexing:
            if not self.doesTableExist("licensedata"):
                return
            self.cursor.execute('CREATE INDEX IF NOT EXISTS licensedataindex ON licensedata ( licensename )')
            self.commitChanges()

    def createLicensesIndex(self):
        if self.indexing:
            self.cursor.execute('CREATE INDEX IF NOT EXISTS licensesindex ON licenses ( license )')
            self.commitChanges()

    def createCategoriesIndex(self):
        if self.indexing:
            self.cursor.execute('CREATE INDEX IF NOT EXISTS categoriesindex_category ON categories ( category )')
            self.commitChanges()

    def createKeywordsIndex(self):
        if self.indexing:
            self.cursor.execute('CREATE INDEX IF NOT EXISTS keywordsreferenceindex ON keywordsreference ( keywordname )')
            self.cursor.execute('CREATE INDEX IF NOT EXISTS keywordsindex_idpackage ON keywords ( idpackage )')
            self.cursor.execute('CREATE INDEX IF NOT EXISTS keywordsindex_idkeyword ON keywords ( idkeyword )')
            self.commitChanges()

    def createDependenciesIndex(self):
        if self.indexing:
            self.cursor.execute('CREATE INDEX IF NOT EXISTS dependenciesindex_idpackage ON dependencies ( idpackage )')
            self.cursor.execute('CREATE INDEX IF NOT EXISTS dependenciesindex_iddependency ON dependencies ( iddependency )')
            self.cursor.execute('CREATE INDEX IF NOT EXISTS dependenciesreferenceindex_dependency ON dependenciesreference ( dependency )')
            self.commitChanges()

    def createCountersIndex(self):
        if self.indexing:
            self.cursor.execute('CREATE INDEX IF NOT EXISTS countersindex_counter ON counters ( counter )')
            self.commitChanges()

    def createSourcesIndex(self):
        if self.indexing:
            self.cursor.execute('CREATE INDEX IF NOT EXISTS sourcesindex_idpackage ON sources ( idpackage )')
            self.cursor.execute('CREATE INDEX IF NOT EXISTS sourcesindex_idsource ON sources ( idsource )')
            self.cursor.execute('CREATE INDEX IF NOT EXISTS sourcesreferenceindex_source ON sourcesreference ( source )')
            self.commitChanges()

    def createProvideIndex(self):
        if self.indexing:
            self.cursor.execute('CREATE INDEX IF NOT EXISTS provideindex_idpackage ON provide ( idpackage )')
            self.cursor.execute('CREATE INDEX IF NOT EXISTS provideindex_atom ON provide ( atom )')
            self.commitChanges()

    def createConflictsIndex(self):
        if self.indexing:
            self.cursor.execute('CREATE INDEX IF NOT EXISTS conflictsindex_idpackage ON conflicts ( idpackage )')
            self.cursor.execute('CREATE INDEX IF NOT EXISTS conflictsindex_atom ON conflicts ( conflict )')
            self.commitChanges()

    def createExtrainfoIndex(self):
        if self.indexing:
            self.cursor.execute('CREATE INDEX IF NOT EXISTS extrainfoindex ON extrainfo ( description )')
            self.commitChanges()

    def createEclassesIndex(self):
        if self.indexing:
            self.cursor.execute('CREATE INDEX IF NOT EXISTS eclassesindex_idpackage ON eclasses ( idpackage )')
            self.cursor.execute('CREATE INDEX IF NOT EXISTS eclassesindex_idclass ON eclasses ( idclass )')
            self.cursor.execute('CREATE INDEX IF NOT EXISTS eclassesreferenceindex_classname ON eclassesreference ( classname )')
            self.commitChanges()

    def regenerateCountersTable(self, vdb_path, output = False):
        self.checkReadOnly()
        self.createCountersTable()
        # assign a counter to an idpackage
        myids = self.listAllIdpackages()
        for myid in myids:
            # get atom
            myatom = self.retrieveAtom(myid)
            mybranch = self.retrieveBranch(myid)
            myatom = self.entropyTools.remove_tag(myatom)
            myatomcounterpath = vdb_path+myatom+"/"+etpConst['spm']['xpak_entries']['counter']
            if os.path.isfile(myatomcounterpath):
                try:
                    f = open(myatomcounterpath,"r")
                    counter = int(f.readline().strip())
                    f.close()
                except:
                    if output: self.updateProgress(red("ATTENTION: cannot open Gentoo counter file for: %s") % (bold(myatom),), importance = 1, type = "warning")
                    continue
                # insert id+counter
                try:
                    self.cursor.execute(
                            'INSERT into counters VALUES '
                            '(?,?,?)', ( counter, myid, mybranch )
                    )
                except:
                    if output: self.updateProgress(red("ATTENTION: counter for atom %s")+red(" is duplicated. Ignoring.") % (bold(myatom),), importance = 1, type = "warning")
                    continue # don't trust counters, they might not be unique
        self.commitChanges()

    def clearTreeupdatesEntries(self, repository):
        self.checkReadOnly()
        if not self.doesTableExist("treeupdates"):
            self.createTreeupdatesTable()
        # treeupdates
        self.cursor.execute("DELETE FROM treeupdates WHERE repository = (?)", (repository,))
        self.commitChanges()

    def resetTreeupdatesDigests(self):
        self.checkReadOnly()
        self.cursor.execute('UPDATE treeupdates SET digest = "-1"')
        self.commitChanges()

    #
    # FIXME: remove these when 1.0 will be out
    #

    def migrateCountersTable(self):
        self.cursor.execute('DROP TABLE IF EXISTS counterstemp;')
        self.cursor.execute('CREATE TABLE counterstemp ( counter INTEGER, idpackage INTEGER PRIMARY KEY, branch VARCHAR );')
        self.cursor.execute('select * from counters')
        countersdata = self.cursor.fetchall()
        if countersdata:
            for row in countersdata:
                self.cursor.execute('INSERT INTO counterstemp VALUES = (?,?,?)',row)
        self.cursor.execute('DROP TABLE counters')
        self.cursor.execute('ALTER TABLE counterstemp RENAME TO counters')
        self.commitChanges()

    def createCategoriesdescriptionTable(self):
        self.cursor.execute('CREATE TABLE categoriesdescription ( category VARCHAR, locale VARCHAR, description VARCHAR );')

    def createTreeupdatesTable(self):
        self.cursor.execute('CREATE TABLE treeupdates ( repository VARCHAR PRIMARY KEY, digest VARCHAR );')

    def createTreeupdatesactionsTable(self):
        self.cursor.execute('CREATE TABLE treeupdatesactions ( idupdate INTEGER PRIMARY KEY AUTOINCREMENT, repository VARCHAR, command VARCHAR, branch VARCHAR, date VARCHAR );')

    def createSizesTable(self):
        self.cursor.execute('CREATE TABLE sizes ( idpackage INTEGER, size INTEGER );')

    def createEntropyMiscCountersTable(self):
        self.cursor.execute('CREATE TABLE entropy_misc_counters ( idtype INTEGER PRIMARY KEY, counter INTEGER );')

    def createDependenciesTypeColumn(self):
        self.cursor.execute('ALTER TABLE dependencies ADD COLUMN type INTEGER;')
        self.cursor.execute('UPDATE dependencies SET type = (?)', (0,))

    def createCountersBranchColumn(self):
        self.cursor.execute('ALTER TABLE counters ADD COLUMN branch VARCHAR;')
        idpackages = self.listAllIdpackages()
        for idpackage in idpackages:
            branch = self.retrieveBranch(idpackage)
            self.cursor.execute('UPDATE counters SET branch = (?) WHERE idpackage = (?)', (branch,idpackage,))

    def createTreeupdatesactionsDateColumn(self):
        try: # if database disk image is malformed, won't raise exception here
            self.cursor.execute('ALTER TABLE treeupdatesactions ADD COLUMN date VARCHAR;')
            mytime = str(self.entropyTools.getCurrentUnixTime())
            self.cursor.execute('UPDATE treeupdatesactions SET date = (?)', (mytime,))
        except:
            pass

    def createTreeupdatesactionsBranchColumn(self):
        try: # if database disk image is malformed, won't raise exception here
            self.cursor.execute('ALTER TABLE treeupdatesactions ADD COLUMN branch VARCHAR;')
            self.cursor.execute('UPDATE treeupdatesactions SET branch = (?)', (str(etpConst['branch']),))
        except:
            pass

    def createNeededElfclassColumn(self):
        try: # if database disk image is malformed, won't raise exception here
            self.cursor.execute('ALTER TABLE needed ADD COLUMN elfclass INTEGER;')
            self.cursor.execute('UPDATE needed SET elfclass = -1')
        except:
            pass

    def createContentTypeColumn(self):
        try: # if database disk image is malformed, won't raise exception here
            self.cursor.execute('ALTER TABLE content ADD COLUMN type VARCHAR;')
            self.cursor.execute('UPDATE content SET type = "0"')
        except:
            pass

    def createLicensedataTable(self):
        self.cursor.execute('CREATE TABLE licensedata ( licensename VARCHAR UNIQUE, text BLOB, compressed INTEGER );')

    def createLicensesAcceptedTable(self):
        self.cursor.execute('CREATE TABLE licenses_accepted ( licensename VARCHAR UNIQUE );')

    def createTrashedcountersTable(self):
        self.cursor.execute('CREATE TABLE trashedcounters ( counter INTEGER );')

    def createTriggerTable(self):
        self.cursor.execute('CREATE TABLE triggers ( idpackage INTEGER PRIMARY KEY, data BLOB );')

    def createTriggerColumn(self):
        self.checkReadOnly()
        self.cursor.execute('ALTER TABLE baseinfo ADD COLUMN trigger INTEGER;')
        self.cursor.execute('UPDATE baseinfo SET trigger = 0')

    def createMessagesTable(self):
        self.cursor.execute("CREATE TABLE messages ( idpackage INTEGER, message VARCHAR );")

    def createEclassesTable(self):
        self.cursor.execute('DROP TABLE IF EXISTS eclasses;')
        self.cursor.execute('DROP TABLE IF EXISTS eclassesreference;')
        self.cursor.execute('CREATE TABLE eclasses ( idpackage INTEGER, idclass INTEGER );')
        self.cursor.execute('CREATE TABLE eclassesreference ( idclass INTEGER PRIMARY KEY AUTOINCREMENT, classname VARCHAR );')

    def createNeededTable(self):
        self.cursor.execute('CREATE TABLE needed ( idpackage INTEGER, idneeded INTEGER, elfclass INTEGER );')
        self.cursor.execute('CREATE TABLE neededreference ( idneeded INTEGER PRIMARY KEY AUTOINCREMENT, library VARCHAR );')

    def createSystemPackagesTable(self):
        self.cursor.execute('CREATE TABLE systempackages ( idpackage INTEGER PRIMARY KEY );')

    def createInjectedTable(self):
        self.cursor.execute('CREATE TABLE injected ( idpackage INTEGER PRIMARY KEY );')

    def createProtectTable(self):
        self.cursor.execute('DROP TABLE IF EXISTS configprotect;')
        self.cursor.execute('DROP TABLE IF EXISTS configprotectmask;')
        self.cursor.execute('DROP TABLE IF EXISTS configprotectreference;')
        self.cursor.execute('CREATE TABLE configprotect ( idpackage INTEGER PRIMARY KEY, idprotect INTEGER );')
        self.cursor.execute('CREATE TABLE configprotectmask ( idpackage INTEGER PRIMARY KEY, idprotect INTEGER );')
        self.cursor.execute('CREATE TABLE configprotectreference ( idprotect INTEGER PRIMARY KEY AUTOINCREMENT, protect VARCHAR );')

    def createInstalledTable(self):
        self.cursor.execute('DROP TABLE IF EXISTS installedtable;')
        self.cursor.execute('CREATE TABLE installedtable ( idpackage INTEGER PRIMARY KEY, repositoryname VARCHAR );')

    def addDependRelationToDependsTable(self, iddependency, idpackage):
        self.cursor.execute(
                'INSERT into dependstable VALUES '
                '(?,?)'
                , (	iddependency,
                        idpackage,
                        )
        )
        if (self.entropyTools.is_user_in_entropy_group()) and \
            (self.dbname.startswith(etpConst['serverdbid'])):
                # force commit even if readonly, this will allow to automagically fix dependstable server side
                self.connection.commit() # we don't care much about syncing the database since it's quite trivial

    '''
       @description: recreate dependstable table in the chosen database, it's used for caching searchDepends requests
       @input Nothing
       @output: Nothing
    '''
    def regenerateDependsTable(self, output = True):
        self.createDependsTable()
        depends = self.listAllDependencies()
        count = 0
        total = len(depends)
        for iddep,atom in depends:
            count += 1
            if output:
                self.updateProgress(
                                        red("Resolving %s") % (darkgreen(atom),),
                                        importance = 0,
                                        type = "info",
                                        back = True,
                                        count = (count,total)
                                    )
            match = self.atomMatch(atom)
            if (match[0] != -1):
                self.addDependRelationToDependsTable(iddep,match[0])
        del depends
        # now validate dependstable
        self.sanitizeDependsTable()


########################################################
####
##   Dependency handling functions
#

    def atomMatchFetchCache(self, *args):
        if self.xcache:
            c_hash = str(hash(tuple(args)))
            try:
                cached = dumpTools.loadobj(etpCache['dbMatch']+"/"+self.dbname+"/"+c_hash)
                if cached != None:
                    return cached
            except (EOFError, IOError):
                return None

    def atomMatchStoreCache(self, *args, **kwargs):
        if self.xcache:
            c_hash = str(hash(tuple(args)))
            try:
                sperms = False
                if not os.path.isdir(os.path.join(etpConst['dumpstoragedir'],etpCache['dbMatch']+"/"+self.dbname)):
                    sperms = True
                dumpTools.dumpobj(etpCache['dbMatch']+"/"+self.dbname+"/"+c_hash,kwargs['result'])
                if sperms:
                    const_setup_perms(etpConst['dumpstoragedir'],etpConst['entropygid'])
            except IOError:
                pass

    # function that validate one atom by reading keywords settings
    # idpackageValidatorCache = {} >> function cache
    def idpackageValidator(self,idpackage):

        if self.dbname == etpConst['clientdbid']:
            return idpackage,0

        reponame = self.dbname[5:]
        cached = idpackageValidatorCache.get((idpackage,reponame))
        if cached != None:
            return cached

        # check if user package.mask needs it masked
        user_package_mask_ids = etpConst['packagemasking'].get(reponame+'mask_ids')
        if user_package_mask_ids == None:
            etpConst['packagemasking'][reponame+'mask_ids'] = set()
            for atom in etpConst['packagemasking']['mask']:
                matches = self.atomMatch(atom, multiMatch = True, packagesFilter = False)
                if matches[1] != 0:
                    continue
                etpConst['packagemasking'][reponame+'mask_ids'] |= set(matches[0])
            user_package_mask_ids = etpConst['packagemasking'][reponame+'mask_ids']
        if idpackage in user_package_mask_ids:
            # sorry, masked
            idpackageValidatorCache[(idpackage,reponame)] = -1,1
            return -1,1

        # see if we can unmask by just lookin into user package.unmask stuff -> etpConst['packagemasking']['unmask']
        user_package_unmask_ids = etpConst['packagemasking'].get(reponame+'unmask_ids')
        if user_package_unmask_ids == None:
            etpConst['packagemasking'][reponame+'unmask_ids'] = set()
            for atom in etpConst['packagemasking']['unmask']:
                matches = self.atomMatch(atom, multiMatch = True, packagesFilter = False)
                if matches[1] != 0:
                    continue
                etpConst['packagemasking'][reponame+'unmask_ids'] |= set(matches[0])
            user_package_unmask_ids = etpConst['packagemasking'][reponame+'unmask_ids']
        if idpackage in user_package_unmask_ids:
            idpackageValidatorCache[(idpackage,reponame)] = idpackage,3
            return idpackage,3

        # check if repository packages.db.mask needs it masked
        repomask = etpConst['packagemasking']['repos_mask'].get(reponame)
        if repomask != None:
            # first, seek into generic masking, all branches
            all_branches_mask = repomask.get("*")
            if all_branches_mask:
                all_branches_mask_ids = repomask.get("*_ids")
                if all_branches_mask_ids == None:
                    etpConst['packagemasking']['repos_mask'][reponame]['*_ids'] = set()
                    for atom in all_branches_mask:
                        matches = self.atomMatch(atom, multiMatch = True, packagesFilter = False)
                        if matches[1] != 0:
                            continue
                        etpConst['packagemasking']['repos_mask'][reponame]['*_ids'] |= set(matches[0])
                    all_branches_mask_ids = etpConst['packagemasking']['repos_mask'][reponame]['*_ids']
                if idpackage in all_branches_mask_ids:
                    idpackageValidatorCache[(idpackage,reponame)] = -1,8
                    return -1,8
            # no universal mask
            branches_mask = repomask.get("branch")
            if branches_mask:
                for branch in branches_mask:
                    branch_mask_ids = branches_mask.get(branch+"_ids")
                    if branch_mask_ids == None:
                        etpConst['packagemasking']['repos_mask'][reponame]['branch'][branch+"_ids"] = set()
                        for atom in branches_mask[branch]:
                            matches = self.atomMatch(atom, multiMatch = True, packagesFilter = False)
                            if matches[1] != 0:
                                continue
                            etpConst['packagemasking']['repos_mask'][reponame]['branch'][branch+"_ids"] |= set(matches[0])
                        branch_mask_ids = etpConst['packagemasking']['repos_mask'][reponame]['branch'][branch+"_ids"]
                    if idpackage in branch_mask_ids:
                        if  self.retrieveBranch(idpackage) == branch:
                            idpackageValidatorCache[(idpackage,reponame)] = -1,9
                            return -1,9

        if etpConst['packagemasking']['license_mask']:
            mylicenses = self.retrieveLicense(idpackage)
            mylicenses = mylicenses.strip().split()
            if mylicenses:
                for mylicense in mylicenses:
                    if mylicense in etpConst['packagemasking']['license_mask']:
                        idpackageValidatorCache[(idpackage,reponame)] = -1,10
                        return -1,10

        mykeywords = self.retrieveKeywords(idpackage)
        # XXX WORKAROUND
        if not mykeywords: mykeywords = [''] # ** is fine then
        # firstly, check if package keywords are in etpConst['keywords'] (universal keywords have been merged from package.mask)
        for key in etpConst['keywords']:
            if key in mykeywords:
                # found! all fine
                idpackageValidatorCache[(idpackage,reponame)] = idpackage,2
                return idpackage,2

        # if we get here, it means we didn't find mykeywords in etpConst['keywords'], we need to seek etpConst['packagemasking']['keywords']
        # seek in repository first
        if reponame in etpConst['packagemasking']['keywords']['repositories']:
            for keyword in etpConst['packagemasking']['keywords']['repositories'][reponame]:
                if keyword in mykeywords:
                    keyword_data = etpConst['packagemasking']['keywords']['repositories'][reponame].get(keyword)
                    if keyword_data:
                        if "*" in keyword_data: # all packages in this repo with keyword "keyword" are ok
                            idpackageValidatorCache[(idpackage,reponame)] = idpackage,4
                            return idpackage,4
                        keyword_data_ids = etpConst['packagemasking']['keywords']['repositories'][reponame].get(keyword+"_ids")
                        if keyword_data_ids == None:
                            etpConst['packagemasking']['keywords']['repositories'][reponame][keyword+"_ids"] = set()
                            for atom in keyword_data:
                                matches = self.atomMatch(atom, multiMatch = True, packagesFilter = False)
                                if matches[1] != 0:
                                    continue
                                etpConst['packagemasking']['keywords']['repositories'][reponame][keyword+"_ids"] |= matches[0]
                            keyword_data_ids = etpConst['packagemasking']['keywords']['repositories'][reponame][keyword+"_ids"]
                        if idpackage in keyword_data_ids:
                            idpackageValidatorCache[(idpackage,reponame)] = idpackage,5
                            return idpackage,5

        # if we get here, it means we didn't find a match in repositories
        # so we scan packages, last chance
        for keyword in etpConst['packagemasking']['keywords']['packages']:
            # first of all check if keyword is in mykeywords
            if keyword in mykeywords:
                keyword_data = etpConst['packagemasking']['keywords']['packages'].get(keyword)
                # check for relation
                if keyword_data:
                    keyword_data_ids = etpConst['packagemasking']['keywords']['packages'][reponame+keyword+"_ids"]
                    if keyword_data_ids == None:
                        etpConst['packagemasking']['keywords']['packages'][reponame+keyword+"_ids"] = set()
                        for atom in keyword_data:
                            # match atom
                            matches = self.atomMatch(atom, multiMatch = True, packagesFilter = False)
                            if matches[1] != 0:
                                continue
                            etpConst['packagemasking']['keywords']['packages'][reponame+keyword+"_ids"] |= matches[0]
                        keyword_data_ids = etpConst['packagemasking']['keywords']['packages'][reponame+keyword+"_ids"]
                    if idpackage in keyword_data_ids:
                        # valid!
                        idpackageValidatorCache[(idpackage,reponame)] = idpackage,6
                        return idpackage,6

        # holy crap, can't validate
        idpackageValidatorCache[(idpackage,reponame)] = -1,7
        return -1,7

    # packages filter used by atomMatch, input must me foundIDs, a list like this:
    # [608,1867]
    def packagesFilter(self, results, atom):
        # keywordsFilter ONLY FILTERS results if
        # self.dbname.startswith(etpConst['dbnamerepoprefix']) => repository database is open
        if not self.dbname.startswith(etpConst['dbnamerepoprefix']):
            return results

        newresults = set()
        for idpackage in results:
            rc = self.idpackageValidator(idpackage)
            if rc[0] != -1:
                newresults.add(idpackage)
            else:
                idreason = rc[1]
                if not maskingReasonsStorage.has_key(atom):
                    maskingReasonsStorage[atom] = {}
                if not maskingReasonsStorage[atom].has_key(idreason):
                    maskingReasonsStorage[atom][idreason] = set()
                maskingReasonsStorage[atom][idreason].add((idpackage,self.dbname[5:]))
        return newresults

    def __filterSlot(self, idpackage, slot):
        if slot == None:
            return idpackage
        dbslot = self.retrieveSlot(idpackage)
        if str(dbslot) == str(slot):
            return idpackage

    def __filterTag(self, idpackage, tag, operators):
        if tag == None:
            return idpackage
        dbtag = self.retrieveVersionTag(idpackage)
        compare = cmp(tag,dbtag)
        if not operators or operators == "=":
            if compare == 0:
                return idpackage
        else:
            return self.__do_operator_compare(idpackage, operators, compare)

    def __do_operator_compare(self, token, operators, compare):
        if operators == ">" and compare == -1:
            return token
        elif operators == ">=" and compare < 1:
            return token
        elif operators == "<" and compare == 1:
            return token
        elif operators == "<=" and compare > -1:
            return token

    def __filterSlotTag(self, foundIDs, slot, tag, operators):

        newlist = set()
        for idpackage in foundIDs:

            idpackage = self.__filterSlot(idpackage, slot)
            if not idpackage:
                continue

            idpackage = self.__filterTag(idpackage, tag, operators)
            if not idpackage:
                continue

            newlist.add(idpackage)

        return newlist

    '''
       @description: matches the user chosen package name+ver, if possibile, in a single repository
       @input atom: string, atom to match
       @input caseSensitive: bool, should the atom be parsed case sensitive?
       @input matchSlot: string, match atoms with the provided slot
       @input multiMatch: bool, return all the available atoms
       @input matchBranches: tuple or list, match packages only in the specified branches
       @input matchTag: match packages only for the specified tag
       @input packagesFilter: enable/disable package.mask/.keywords/.unmask filter
       @output: the package id, if found, otherwise -1 plus the status, 0 = ok, 1 = error
    '''
    def atomMatch(self, atom, caseSensitive = True, matchSlot = None, multiMatch = False, matchBranches = (), matchTag = None, packagesFilter = True, matchRevision = None, extendedResults = False):

        if not atom:
            return -1,1

        cached = self.atomMatchFetchCache(
            atom,
            caseSensitive,
            matchSlot,
            multiMatch,
            matchBranches,
            matchTag,
            packagesFilter,
            matchRevision,
            extendedResults
        )
        if cached != None:
            return cached

        atomTag = self.entropyTools.dep_gettag(atom)
        atomSlot = self.entropyTools.dep_getslot(atom)
        atomRev = self.entropyTools.dep_get_entropy_revision(atom)

        # tag match
        scan_atom = self.entropyTools.remove_tag(atom)
        if (matchTag == None) and (atomTag != None):
            matchTag = atomTag

        # slot match
        scan_atom = self.entropyTools.remove_slot(scan_atom)
        if (matchSlot == None) and (atomSlot != None):
            matchSlot = atomSlot

        # revision match
        scan_atom = self.entropyTools.remove_entropy_revision(scan_atom)
        if (matchRevision == None) and (atomRev != None):
            matchRevision = atomRev

        # check for direction
        strippedAtom = self.entropyTools.dep_getcpv(scan_atom)
        if scan_atom[-1] == "*":
            strippedAtom += "*"
        direction = scan_atom[0:len(scan_atom)-len(strippedAtom)]

        justname = self.entropyTools.isjustname(strippedAtom)
        pkgversion = ''
        if not justname:

            # get version
            data = self.entropyTools.catpkgsplit(strippedAtom)
            if data == None:
                return -1,1 # atom is badly formatted
            pkgversion = data[2]+"-"+data[3]

        pkgkey = self.entropyTools.dep_getkey(strippedAtom)
        splitkey = pkgkey.split("/")
        if (len(splitkey) == 2):
            pkgname = splitkey[1]
            pkgcat = splitkey[0]
        else:
            pkgname = splitkey[0]
            pkgcat = "null"

        if matchBranches:
            # force to tuple for security
            myBranchIndex = tuple(matchBranches)
        else:
            if self.dbname == etpConst['clientdbid']:
                # collect all available branches
                myBranchIndex = tuple(self.listAllBranches())
            elif self.dbname.startswith(etpConst['dbnamerepoprefix']):
                # repositories should match to any branch <= than the current if none specified
                allbranches = set([x for x in self.listAllBranches() if x <= etpConst['branch']])
                allbranches = list(allbranches)
                allbranches.reverse()
                if etpConst['branch'] not in allbranches:
                    allbranches.insert(0,etpConst['branch'])
                myBranchIndex = tuple(allbranches)
            else:
                myBranchIndex = (etpConst['branch'],)

        # IDs found in the database that match our search
        foundIDs = set()

        for idx in myBranchIndex:

            if pkgcat == "null":
                results = self.searchPackagesByName(
                                    pkgname,
                                    sensitive = caseSensitive,
                                    branch = idx,
                                    justid = True
                )
            else:
                results = self.searchPackagesByNameAndCategory(
                                    name = pkgname,
                                    category = pkgcat,
                                    branch = idx,
                                    sensitive = caseSensitive,
                                    justid = True
                )

            mypkgcat = pkgcat
            mypkgname = pkgname
            virtual = False
            # if it's a PROVIDE, search with searchProvide
            # there's no package with that name
            if (not results) and (mypkgcat == "virtual"):
                virtuals = self.searchProvide(pkgkey, branch = idx, justid = True)
                if virtuals:
                    virtual = True
                    mypkgname = self.retrieveName(virtuals[0])
                    mypkgcat = self.retrieveCategory(virtuals[0])
                    results = virtuals

            # now validate
            if not results:
                continue # search into a stabler branch

            elif (len(results) > 1):

                # if it's because category differs, it's a problem
                foundCat = None
                cats = set()
                for idpackage in results:
                    cat = self.retrieveCategory(idpackage)
                    cats.add(cat)
                    if (cat == mypkgcat) or ((not virtual) and (mypkgcat == "virtual") and (cat == mypkgcat)):
                        # in case of virtual packages only (that they're not stored as provide)
                        foundCat = cat

                # if we found something at least...
                if (not foundCat) and (len(cats) == 1) and (mypkgcat in ("virtual","null")):
                    foundCat = list(cats)[0]

                if not foundCat:
                    # got the issue
                    continue

                # we can use foundCat
                mypkgcat = foundCat

                # we need to search using the category
                if (not multiMatch) and (pkgcat == "null" or virtual):
                    # we searched by name, we need to search using category
                    results = self.searchPackagesByNameAndCategory(
                                        name = mypkgname,
                                        category = mypkgcat,
                                        branch = idx,
                                        sensitive = caseSensitive,
                                        justid = True
                    )

                # validate again
                if not results:
                    continue  # search into another branch

                # if we get here, we have found the needed IDs
                foundIDs |= set(results)
                break

            else:

                idpackage = results[0]
                # if mypkgcat is virtual, we can force
                if (mypkgcat == "virtual") and (not virtual):
                    # in case of virtual packages only (that they're not stored as provide)
                    mypkgcat = self.retrieveCategory(idpackage)

                # check if category matches
                if mypkgcat != "null":
                    foundCat = self.retrieveCategory(idpackage)
                    if mypkgcat == foundCat:
                        foundIDs.add(idpackage)
                    else:
                        continue
                else:
                    foundIDs.add(idpackage)
                    break

        ### FILTERING
        ### FILTERING
        ### FILTERING

        # filter slot and tag
        foundIDs = self.__filterSlotTag(foundIDs, matchSlot, matchTag, direction)

        if packagesFilter: # keyword filtering
            foundIDs = self.packagesFilter(foundIDs, atom)

        ### END FILTERING
        ### END FILTERING
        ### END FILTERING

        if not foundIDs:
            # package not found
            self.atomMatchStoreCache(atom, caseSensitive, matchSlot, multiMatch, matchBranches, matchTag, packagesFilter, matchRevision, extendedResults, result = (-1,1))
            return -1,1

        ### FILLING dbpkginfo
        ### FILLING dbpkginfo
        ### FILLING dbpkginfo

        dbpkginfo = set()
        # now we have to handle direction
        if (direction) or (direction == '' and not justname) or (direction == '' and not justname and strippedAtom.endswith("*")):

            if (not justname) and \
                ((direction == "~") or (direction == "=") or \
                (direction == '' and not justname) or (direction == '' and not justname and strippedAtom.endswith("*"))):
                # any revision within the version specified OR the specified version

                if (direction == '' and not justname):
                    direction = "="

                # remove gentoo revision (-r0 if none)
                if (direction == "="):
                    if (pkgversion.split("-")[-1] == "r0"):
                        pkgversion = self.entropyTools.remove_revision(pkgversion)
                if (direction == "~"):
                    pkgrevision = self.entropyTools.dep_get_portage_revision(pkgversion)
                    pkgversion = self.entropyTools.remove_revision(pkgversion)

                for idpackage in foundIDs:

                    dbver = self.retrieveVersion(idpackage)
                    if (direction == "~"):
                        myrev = self.entropyTools.dep_get_portage_revision(dbver)
                        myver = self.entropyTools.remove_revision(dbver)
                        if myver == pkgversion and pkgrevision <= myrev:
                            # found
                            dbpkginfo.add((idpackage,dbver))
                    else:
                        # media-libs/test-1.2* support
                        if pkgversion[-1] == "*":
                            if dbver.startswith(pkgversion[:-1]):
                                dbpkginfo.add((idpackage,dbver))
                        elif (matchRevision != None) and (pkgversion == dbver):
                            dbrev = self.retrieveRevision(idpackage)
                            if dbrev == matchRevision:
                                dbpkginfo.add((idpackage,dbver))
                        elif (pkgversion == dbver) and (matchRevision == None):
                            dbpkginfo.add((idpackage,dbver))

            elif (direction.find(">") != -1) or (direction.find("<") != -1):

                if not justname:

                    # remove revision (-r0 if none)
                    if pkgversion.endswith("r0"):
                        # remove
                        self.entropyTools.remove_revision(pkgversion)

                    for idpackage in foundIDs:

                        revcmp = 0
                        tagcmp = 0
                        if matchRevision != None:
                            dbrev = self.retrieveRevision(idpackage)
                            revcmp = cmp(matchRevision,dbrev)
                        if matchTag != None:
                            dbtag = self.retrieveVersionTag(idpackage)
                            tagcmp = cmp(matchTag,dbtag)
                        dbver = self.retrieveVersion(idpackage)
                        pkgcmp = self.entropyTools.compareVersions(pkgversion,dbver)
                        if isinstance(pkgcmp,tuple):
                            failed = pkgcmp[1]
                            if failed == 0:
                                failed = pkgversion
                            else:
                                failed = dbver
                            # I am sorry, but either pkgversion or dbver are invalid
                            self.updateProgress(
                                                    bold("atomMatch: ")+red("comparison between %s and %s failed. Wrong syntax for: %s") % (pkgversion,dbver,failed,),
                                                    importance = 1,
                                                    type = "error",
                                                    header = darkred(" !!! ")
                                                )
                            raise exceptionTools.InvalidVersionString(
                                            "InvalidVersionString: from atom: %s, cmp: %s, failed: %s" % (
                                                atom, pkgcmp, failed, )
                                            )
                        if direction == ">":
                            if pkgcmp < 0:
                                dbpkginfo.add((idpackage,dbver))
                            elif (matchRevision != None) and pkgcmp <= 0 and revcmp < 0:
                                #print "found >",self.retrieveAtom(idpackage)
                                dbpkginfo.add((idpackage,dbver))
                            elif (matchTag != None) and tagcmp < 0:
                                dbpkginfo.add((idpackage,dbver))
                        elif direction == "<":
                            if pkgcmp > 0:
                                dbpkginfo.add((idpackage,dbver))
                            elif (matchRevision != None) and pkgcmp >= 0 and revcmp > 0:
                                #print "found <",self.retrieveAtom(idpackage)
                                dbpkginfo.add((idpackage,dbver))
                            elif (matchTag != None) and tagcmp > 0:
                                dbpkginfo.add((idpackage,dbver))
                        elif direction == ">=":
                            if (matchRevision != None) and pkgcmp <= 0:
                                if pkgcmp == 0:
                                    if revcmp <= 0:
                                        dbpkginfo.add((idpackage,dbver))
                                        #print "found >=",self.retrieveAtom(idpackage)
                                else:
                                    dbpkginfo.add((idpackage,dbver))
                            elif pkgcmp <= 0 and matchRevision == None:
                                dbpkginfo.add((idpackage,dbver))
                            elif (matchTag != None) and tagcmp <= 0:
                                dbpkginfo.add((idpackage,dbver))
                        elif direction == "<=":
                            if (matchRevision != None) and pkgcmp >= 0:
                                if pkgcmp == 0:
                                    if revcmp >= 0:
                                        dbpkginfo.add((idpackage,dbver))
                                        #print "found <=",self.retrieveAtom(idpackage)
                                else:
                                    dbpkginfo.add((idpackage,dbver))
                            elif pkgcmp >= 0 and matchRevision == None:
                                dbpkginfo.add((idpackage,dbver))
                            elif (matchTag != None) and tagcmp >= 0:
                                dbpkginfo.add((idpackage,dbver))

        else: # just the key

            dbpkginfo = set([(x,self.retrieveVersion(x)) for x in foundIDs])

        ### END FILLING dbpkginfo
        ### END FILLING dbpkginfo
        ### END FILLING dbpkginfo

        if not dbpkginfo:
            if extendedResults:
                x = (-1,1,None,None,None)
                self.atomMatchStoreCache(atom, caseSensitive, matchSlot, multiMatch, matchBranches, matchTag, packagesFilter, matchRevision, extendedResults, result = x)
                return x
            else:
                self.atomMatchStoreCache(atom, caseSensitive, matchSlot, multiMatch, matchBranches, matchTag, packagesFilter, matchRevision, extendedResults, result = (-1,1))
                return -1,1

        if multiMatch:
            if extendedResults:
                x = set([(x[0],0,x[1],self.retrieveVersionTag(x[0]),self.retrieveRevision(x[0])) for x in dbpkginfo]),0
                self.atomMatchStoreCache(atom, caseSensitive, matchSlot, multiMatch, matchBranches, matchTag, packagesFilter, matchRevision, extendedResults, result = x)
                return x
            else:
                x = set([x[0] for x in dbpkginfo])
                self.atomMatchStoreCache(atom, caseSensitive, matchSlot, multiMatch, matchBranches, matchTag, packagesFilter, matchRevision, extendedResults, result = (x,0))
                return x,0

        if len(dbpkginfo) == 1:
            x = dbpkginfo.pop()
            if extendedResults:
                x = (x[0],0,x[1],self.retrieveVersionTag(x[0]),self.retrieveRevision(x[0])),0
                self.atomMatchStoreCache(atom, caseSensitive, matchSlot, multiMatch, matchBranches, matchTag, packagesFilter, matchRevision, extendedResults, result = x)
                return x
            else:
                self.atomMatchStoreCache(atom, caseSensitive, matchSlot, multiMatch, matchBranches, matchTag, packagesFilter, matchRevision, extendedResults, result = (x[0],0))
                return x[0],0

        dbpkginfo = list(dbpkginfo)
        pkgdata = {}
        versions = set()
        for x in dbpkginfo:
            info_tuple = (x[1],self.retrieveVersionTag(x[0]),self.retrieveRevision(x[0]))
            versions.add(info_tuple)
            pkgdata[info_tuple] = x[0]
        newer = self.entropyTools.getEntropyNewerVersion(list(versions))[0]
        x = pkgdata[newer]
        if extendedResults:
            x = (x,0,newer[0],newer[1],newer[2]),0
            self.atomMatchStoreCache(atom, caseSensitive, matchSlot, multiMatch, matchBranches, matchTag, packagesFilter, matchRevision, extendedResults, result = x)
            return x
        else:
            self.atomMatchStoreCache(atom, caseSensitive, matchSlot, multiMatch, matchBranches, matchTag, packagesFilter, matchRevision, extendedResults, result = (x,0))
            return x,0
