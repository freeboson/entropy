#!/usr/bin/python
# Copyright Fabio Erculiani - Sabayon Linux 2007

# DESCRIPTION:
# generic tools for all the handlers applications

import portage
import portage_const

from entropyConstants import *
import commands

# resolve atoms automagically
# sys-libs/application --> sys-libs/application-1.2.3-r1
def getBestAtom(atom):
    rc = portage.portdb.xmatch("bestmatch-visible",str(atom))
    return rc

def print_error(msg):
    print "* erro *  : "+msg

def print_info(msg):
    print "* info *  : "+msg

def print_help():
    print "* IIIII * : Sabayon Linux binary-metafile-builder - written by Fabio Erculiani (C - 2007)"
    print "* usage * : binary-metafile-builder <valid gentoo .tbz2 file>"

def print_warning(msg):
    print "* warn *  : "+msg

def print_generic(msg): # here we'll wrap any nice formatting
    print msg

# This function extracts all the info from a .tbz2 file and returns them
def extractPkgData(package):

    tbz2File = package

    package = package.split(".tbz2")
    package = package[0].split("-")
    pkgname = ""
    pkglen = len(package)
    if package[pkglen-1].startswith("r"):
        pkgver = package[pkglen-2]+"-"+package[pkglen-1]
	pkglen -= 2
    else:
	pkgver = package[len(package)-1]
	pkglen -= 1
    for i in range(pkglen):
	if i == pkglen-1:
	    pkgname += package[i]
	else:
	    pkgname += package[i]+"-"
    pkgname = pkgname.split("/")[len(pkgname.split("/"))-1]
    
    # Fill Package name and version
    pData['name'] = pkgname
    pData['version'] = pkgver  # FIXME: add -etpNN

    import xpak
    tbz2 = xpak.tbz2(tbz2File)
    tbz2TmpDir = pTmpDir+"/"+pData['name']+"-"+pData['version']+"/"
    tbz2.decompose(tbz2TmpDir)
    

    # Fill description
    f = open(tbz2TmpDir+dbDESCRIPTION,"r")
    pData['description'] = f.readline().strip()
    f.close()

    # Fill homepage
    f = open(tbz2TmpDir+dbHOMEPAGE,"r")
    pData['homepage'] = f.readline().strip()
    f.close()

    # Fill chost
    f = open(tbz2TmpDir+dbCHOST,"r")
    pData['chost'] = f.readline().strip()
    f.close()

    # Fill url
    pData['download'] = pBinHost+pData['name']+"-"+pData['version']+".tbz2"

    # Fill category
    f = open(tbz2TmpDir+dbCATEGORY,"r")
    pData['category'] = f.readline().strip()
    f.close()

    # Fill CFLAGS
    f = open(tbz2TmpDir+dbCFLAGS,"r")
    pData['cflags'] = f.readline().strip()
    f.close()

    # Fill CXXFLAGS
    f = open(tbz2TmpDir+dbCXXFLAGS,"r")
    pData['cxxflags'] = f.readline().strip()
    f.close()

    # Fill license
    f = open(tbz2TmpDir+dbLICENSE,"r")
    pData['license'] = f.readline().strip()
    f.close()

    # Fill sources
    f = open(tbz2TmpDir+dbSRC_URI,"r")
    pData['sources'] = f.readline().strip()
    f.close()

    # Fill USE
    f = open(tbz2TmpDir+dbUSE,"r")
    tmpUSE = f.readline().strip()
    f.close()
    f = open(tbz2TmpDir+dbIUSE,"r")
    tmpIUSE = f.readline().strip().split()
    f.close()
    
    for i in tmpIUSE:
	if tmpUSE.find(i) != -1:
	    pData['useflags'] += i+" "
	else:
	    pData['useflags'] += "-"+i+" "
    if pData['useflags'].endswith(" "):
        pData['useflags'] = pData['useflags'][:len(pData['useflags'])-1]

    # Fill dependencies
    # to fill dependencies we use *DEPEND files
    f = open(tbz2TmpDir+dbDEPEND,"r")
    roughDependencies = f.readline().strip()
    f.close()
    f = open(tbz2TmpDir+dbRDEPEND,"r")
    roughDependencies += " "+f.readline().strip()
    f.close()
    f = open(tbz2TmpDir+dbPDEPEND,"r")
    roughDependencies += " "+f.readline().strip()
    f.close()
    roughDependencies = roughDependencies.split()
    
    # variables filled
    # pData['dependencies']

    useMatch = False
    openParenthesis = 0
    openOr = False
    useFlagQuestion = False
    for atom in roughDependencies:

	if atom.endswith("?"):
	    # we need to see if that useflag is enabled
	    useFlag = atom.split("?")[0]
	    useFlagQuestion = True
	    for i in pData['useflags'].split():
	        if (i == useFlag):
		    useMatch = True
		    break

        if atom.startswith("("):
	    openParenthesis += 1

        if atom.startswith(")"):
	    if (openOr):
		# remove last "_or_" from pData['dependencies']
		openOr = False
		if pData['dependencies'].endswith(dbOR):
		    pData['dependencies'] = pData['dependencies'][:len(pData['dependencies'])-len(dbOR)]
		    pData['dependencies'] += " "
	    openParenthesis -= 1

        if atom.startswith("||"):
	    openOr = True
	
	if atom.find("/") != -1 and (not atom.startswith("!")):
	    # it's a package name <pkgcat>/<pkgname>-???
	    if ((useFlagQuestion) and (useMatch)):
	        # check if there's an OR
		pData['dependencies'] += atom
		if (openOr):
		    pData['dependencies'] += dbOR
                else:
		    pData['dependencies'] += " "

        if atom.startswith("!"):
	    pData['conflicts'] += atom+" "

    # format properly
    tmpConflicts = list(set(pData['conflicts'].split()))
    pData['conflicts'] = ''
    for i in tmpConflicts:
	pData['conflicts'] += i+" "
    if pData['conflicts'].endswith(" "):
	pData['conflicts'] = pData['conflicts'][:len(pData['conflicts'])-1]

    tmpDeps = list(set(pData['dependencies'].split()))
    pData['dependencies'] = ''
    for i in tmpDeps:
	pData['dependencies'] += i+" "
    if pData['dependencies'].endswith(" "):
	pData['dependencies'] = pData['dependencies'][:len(pData['dependencies'])-1]

    # pData['rdependencies']
    # Now we need to add environmental dependencies
    # Notes (take the example of mplayer that needed a newer libcaca release):
    # - we can use (from /var/db) "NEEDED" file to catch all the needed libraries to run the binary package
    # - we can use (from /var/db) "CONTENTS" to rapidly search the NEEDED files in the file above
    # return all the collected info

    # start collecting needed libraries
    f = open(tbz2TmpDir+"/"+dbNEEDED,"r")
    includedBins = f.readlines()
    f.close()
    
    neededLibraries = []
    # filter the first word
    for line in includedBins:
        line = line.strip().split()
	line = line[0]
	depLibs = commands.getoutput("ldd "+line).split("\n")
	for i in depLibs:
	    i = i.strip()
	    if i.find("=>") != -1:
	        i = i.split("=>")[1]
	    # format properly
	    if i.startswith(" "):
	        i = i[1:]
	    if i.startswith("//"):
	        i = i[1:]
	    i = i.split()[0]
	    neededLibraries.append(i)
    neededLibraries = list(set(neededLibraries))

    runtimeNeededPackages = []
    for i in neededLibraries:
	pkgs = commands.getoutput(pFindLibrary+i).split("\n")
	if (pkgs[0] != ""):
	    for y in pkgs:
	        runtimeNeededPackages.append(y)

    runtimeNeededPackages = list(set(runtimeNeededPackages))
    
    # now keep only the ones not available in pData['dependencies']
    for i in runtimeNeededPackages:
        if pData['dependencies'].find(i) == -1:
	    # filter itself
	    if (i != pData['category']+"/"+pData['name']):
	        pData['rundependencies'] += i+" "
	        i = getBestAtom(i)
	        pData['rundependenciesXT'] += i+" "
    # format properly
    if pData['rundependencies'].endswith(" "):
	pData['rundependencies'] = pData['rundependencies'][:len(pData['rundependencies'])-1]
    if pData['rundependenciesXT'].endswith(" "):
	pData['rundependenciesXT'] = pData['rundependenciesXT'][:len(pData['rundependenciesXT'])-1]

    return pData
