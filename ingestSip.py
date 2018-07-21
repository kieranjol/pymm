#!/usr/bin/env python3
'''
`ingestSip` takes an input a/v file or directory of a/v files,
transcodes a derivative for each file,
produces/extracts some metadata,
creates fixity checks,
and packages the whole lot in an OAIS-like Archival Information Package

@fixme = stuff to do
@logme = stuff to add to ingest log
@dbme = stuff to add to PREMIS db
'''
# standard library modules
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
# local modules:
from bampfa_pbcore import pbcore, makePbcore
import concatFiles
import dbReporters
import makeDerivs
import moveNcopy
import makeMetadata
import premisSQL
import pymmFunctions

# read in from the config file
config = pymmFunctions.read_config()
# # check that paths required for ingest are declared in config.ini
pymmFunctions.check_missing_ingest_paths(config)

def set_args():
	parser = argparse.ArgumentParser()
	parser.add_argument(
		'-i','--inputPath',
		help='path of input file'
		)
	parser.add_argument(
		'-u','--operator',
		help='name of the person doing the ingest'
		)
	parser.add_argument(
		'-j','--metadataJSON',
		help='full path to a JSON file containing descriptive metadata'
		)
	parser.add_argument(
		'-t','--ingestType',
		choices=['film scan','video transfer'],
		default='video transfer',
		help='type of file(s) being ingested: film scan, video xfer'
		)
	parser.add_argument(
		'-p','--makeProres',
		action='store_true',
		help='override whatever config default you have set '
			'and make a prores HQ mezzanine file'
		)
	parser.add_argument(
		'-c','--concatAccessFiles',
		action='store_true',
		help='try to concatenate access files after ingest'
		)
	parser.add_argument(
		'-d','--database_reporting',
		action='store_true',
		default=False,
		help='report preservation metadata/events to database'
		)
	parser.add_argument(
		'-x','--interactiveMode',
		action='store_true',
		help='enter interactive mode for command line usage'
		)
	parser.add_argument(
		'-z','--cleanup_originals',
		action='store_true',
		default=False,
		help='set this flag to delete source files after ingest'
		)

	return parser.parse_args()

def prep_package(tempID):
	'''
	Create a directory structure for a SIP
	'''
	packageOutputDir = os.path.join(config['paths']['outdir_ingestfile'],tempID)
	packageObjectDir = os.path.join(packageOutputDir,'objects')
	packageMetadataDir = os.path.join(packageOutputDir,'metadata')
	packageMetadataObjects = os.path.join(packageMetadataDir,'objects')
	packageLogDir = os.path.join(packageMetadataDir,'logs')
	packageDirs = [packageOutputDir,packageObjectDir,packageMetadataDir,packageMetadataObjects,packageLogDir]
	
	# ... SEE IF THE TOP DIR EXISTS ...
	if os.path.isdir(packageOutputDir):
		print('''
			It looks like {} was already ingested.
			If you want to replace the existing package please delete the package at
			{}
			and then try again.
			'''.format(tempID,packageOutputDir))
		return False

	# ... AND IF NOT, MAKE THEM ALL
	for directory in packageDirs:
		os.mkdir(directory)

	return packageDirs

def sniff_input(inputPath,ingestUUID):#,concatChoice):
	'''
	Check whether the input path from command line is a directory
	or single file. 
	If it's a directory, check that the filenames
	make sense together or if there are any outliers.
	'''
	inputType = pymmFunctions.dir_or_file(inputPath)
	if inputType == 'dir':
		# ADD FUNC TO CLEAN OUT SYSTEM FILES
		# filename sanity check
		goodNames = pymmFunctions.check_for_outliers(inputPath)
		if goodNames:
			print("input is a directory")
			# if concatChoice == True:
				# try_concat(inputPath,ingestUUID)
		else:
			return False
	
	else:
		print("input is a single file")
	return inputType

def concat_access_files(inputPath,ingestUUID,canonicalName,wrapper,\
	ingestLogBoilerplate,processingVars):
	sys.argv = [
		'',
		'-i'+inputPath,
		'-d'+ingestUUID,
		'-c'+canonicalName,
		'-w'+wrapper
		]
	try:
		# concattedAccessFile is either a path to the file
		# or a list of problems with the concatenation.
		concattedAccessFile,success = concatFiles.main()
	except:
		print("couldn't concat files")

	origFilename = processingVars['filename']
	if not success == False:
		outcome = (
			"Component files concatenated "
			"into an access copy at {}".format(concattedAccessFile)
			)
		status = "OK"
		# set the 'filenaeme' to the concat file so we can log in the db
		origFilename = processingVars['filename']
		processingVars['filename'] = os.path.basename(concattedAccessFile)
		processingVars = pymmFunctions.insert_object(
			processingVars,
			'file'
			)
	else:
		status = "FAIL"
		outcome = (
			"Component files could not be concatenated. "
			"Probably you need to check the file specs? "
			"Here's the output of the attempt:\n{}\n"
			"".format(concattedAccessFile)
			)
	processingVars['caller'] = processingVars['ffmpeg']
	pymmFunctions.log_event(
		processingVars,
		ingestLogBoilerplate,
		event='creation',
		outcome=outcome,
		status=status
		)
	processingVars['caller'] = None
	processingVars['filename'] = origFilename

	return concattedAccessFile

def deliver_concat_access(concatPath,accessPath):
	print(concatPath)
	print(accessPath)
	try:
		shutil.copy2(concatPath,accessPath)
		return True
	except:
		print('couldnt deliver the concat file')
		return False

def check_av_status(inputPath,interactiveMode,ingestLogBoilerplate,processingVars):
	'''
	Check whether or not a file is recognized as an a/v file.
	If it isn't and user declares interactive mode,
		ask whether to continue, otherwise quit.
	'''
	event = 'format identification'
	processingVars['caller'] = 'pymmFunctions.is_av()'
	if not pymmFunctions.is_av(inputPath):
		_is_av = False
		outcome = "WARNING: {} is not recognized as an a/v file.".format(
			ingestLogBoilerplate['filename']
			)
		status = "WARNING"
		print(outcome)

		if interactiveMode:
			stayOrGo = input("If you want to quit press 'q' and hit enter, otherwise press any other key:")
			if stayOrGo == 'q':
				# CLEANUP AND LOG THIS @fixme
				sys.exit()
			else:
				print("\nPROCEEDING... WARNING!\n")
	else:
		# THIS IS NOT CORRECT: 
		# THIS NEEDS AN _IS_AV TEST HERE @FIXME
		outcome = ingestLogBoilerplate['filename']+" is an AV file, way to go."
		status = "OK"
	pymmFunctions.log_event(
		processingVars,
		ingestLogBoilerplate,
		event,
		outcome,
		status
		)
	processingVars['caller'] = None

def mediaconch_check(inputPath,ingestType,ingestLogBoilerplate):
	'''
	Check input file against MediaConch policy.
	Needs to be cleaned up. Move logic to pymmFunctions and keep logging here.
	Also, we don't have any policies set up yet...
	'''
	if ingestType == 'film scan':
		policyStatus = pymmFunctions.check_policy(ingestType,inputPath)
		if policyStatus:
			message = filename+" passed the MediaConch policy check."
			status = "ok"
		else:
			message = filename+" did not pass the MediaConch policy check."
			status = "not ok, but not critical?"

		pymmFunctions.ingest_log(
			message,
			status,
			**ingestLogBoilerplate
			)

def move_input_file(processingVars,ingestLogBoilerplate):
	'''
	Put the input file into the package object dir.
	'''
	objectDir = processingVars['packageObjectDir']
	sys.argv = [
		'',
		'-i'+processingVars['inputPath'],
		'-d'+objectDir,
		'-L'+processingVars['packageLogDir']
		]
	event = 'replication'
	outcome = 'migrate file to SIP at {}'.format(objectDir)
	processingVars['caller'] = 'rsync'
	try:
		moveNcopy.main()
		status = 'OK'
	except:
		status = 'FAIL'
	pymmFunctions.log_event(
		processingVars,
		ingestLogBoilerplate,
		event,
		outcome,
		status
		)
	processingVars['caller'] = None

def get_file_metadata(ingestLogBoilerplate,processingVars,_type=None):
	# there's a calculation of the file hash here
	# that really should be left to the object manifest
	# to avoid excess processing drain
	# @fixme
	inputFile = processingVars['inputPath']
	filename = os.path.basename(inputFile)
	# if _type == 'derivative':
	# 	inputFileMD5 = makeMetadata.hash_file(inputFile)

	# processingVars['caller'] = 'Python3 hashlib'
	# eventID = pymmFunctions.short_log(
	# 	processingVars,
	# 	ingestLogBoilerplate,
	# 	event = 'message digest calculation',
	# 	outcome = "The input file MD5 hash is: {}".format(inputFileMD5),
	# 	status = "OK"
	# 	)

	# pymmFunctions.insert_fixity(
	# 	processingVars,
	# 	eventID,
	# 	messageDigestAlgorithm = "md5",
	# 	messageDigestHashValue = inputFileMD5,
	# 	eventDateTime = None
	# 	)
	# processingVars['caller'] = None

	mediainfo = makeMetadata.get_mediainfo_report(
		processingVars['inputPath'],
		processingVars['packageMetadataObjects']
		)
	if mediainfo:
		event = 'metadata extraction'
		outcome = ("mediainfo XML report for input file "
			"written to metadata directory for package.")
		processingVars['caller'] = '`mediainfo --Output=XML`'
		pymmFunctions.short_log(
			processingVars,
			ingestLogBoilerplate,
			event,
			outcome,
			status='OK'
			)
		processingVars['caller'] = None
		processingVars['componentObjectData'][filename]['mediainfoPath'] = mediainfo
	
	if not _type == 'derivative':
	# don't bother calculating frame md5 for derivs....
		frameMD5 = makeMetadata.make_frame_md5(
			processingVars['inputPath'],
			processingVars['packageMetadataObjects']
			)
		if frameMD5 != False:
			event = 'message digest calculation'
			outcome = ("frameMD5 report for input file "
				"written to metadata directory for package")
			processingVars['caller'] = processingVars['ffmpeg']+' with option `-f frameMD5`'
			pymmFunctions.short_log(
				processingVars,
				ingestLogBoilerplate,
				event,
				outcome,
				status='OK'
				)
			processingVars['caller'] = None

	return mediainfo

def add_pbcore_md5_location(processingVars):
	'''
	as of 7/20/2018 have to call this after 
	creation of object manifest for SIP
	and parsing of manifest by report_SIP_fixity()
	'''
	if processingVars['pbcore'] != '':
		pbcoreFile = processingVars['pbcore']
		pbcoreXML = pbcore.PBCoreDocument(pbcoreFile)		
		for _object, data in processingVars['componentObjectData'].items():
			# look for component files and add instantiation data
			if data['objectCategory'] == 'file':
				# add md5 as an identifier to the 
				# pbcoreInstantiation for the file
				# processingVars['filename'] = _object
				inputFileMD5 = data['md5hash']
				attributes = {
					"source":"BAMPFA {}".format(
						pymmFunctions.timestamp('iso8601')
						),
					"annotation":"messageDigest",
					"version":"MD5"
				}
				makePbcore.add_element_to_instantiation(
					pbcoreXML,
					_object,
					'instantiationIdentifier',
					attributes,
					inputFileMD5
					)
				# add 'BAMPFA Digital Repository' as instantiationLocation
				attributes = {}
				makePbcore.add_element_to_instantiation(
					pbcoreXML,
					_object,
					'instantiationLocation',
					attributes,
					"BAMPFA Digital Repository"
					)
		makePbcore.xml_to_file(
			pbcoreXML,
			pbcoreFile
			)

def add_pbcore_instantiation(processingVars,ingestLogBoilerplate,level):
	_file = processingVars['inputPath']
	pbcoreReport = makeMetadata.get_mediainfo_pbcore(_file)
	# print(pbcoreReport)
	descriptiveJSONpath = processingVars['objectBAMPFAjson']
	pbcoreFile = processingVars['pbcore']
	pbcoreXML = pbcore.PBCoreDocument(pbcoreFile)

	event = 'metadata modification'
	outcome = 'add pbcore instantiation representation'
	processingVars['caller'] = 'makePbcore.add_instantiation()'
	try:
		status = 'OK'
		makePbcore.add_instantiation(
			pbcoreXML,
			pbcoreReport,
			descriptiveJSONpath=descriptiveJSONpath,
			level=level
			)
		makePbcore.xml_to_file(pbcoreXML,pbcoreFile)

		pymmFunctions.short_log(
			processingVars,
			ingestLogBoilerplate,
			event,
			outcome,
			status
			)

	except:
		outcome = 'could not '+outcome
		status = 'FAIL'
		pymmFunctions.short_log(
			processingVars,
			ingestLogBoilerplate,
			event,
			outcome,
			status
			)
	# reset 'caller'
	processingVars['caller'] = None

	return processingVars

def make_rs_package(inputObject,rsPackage):
	'''
	If the ingest input is a dir of files, put all the _lrp access files
	into a folder named for the object
	'''
	rsPackageDelivery = ''
	if rsPackage != None:
		try:
			rsOutDir = config['paths']['resourcespace_deliver']
			_object = os.path.basename(inputObject)
			rsPackageDelivery = os.path.join(rsOutDir,_object)

			if not os.path.isdir(rsPackageDelivery):
				try:
					os.mkdir(rsPackageDelivery)
					# add a trailing slash for rsync
					rsPackageDelivery = os.path.join(rsPackageDelivery,'')
					print(rsPackageDelivery)
				except OSError as e:
					print("OOPS: {}".format(e))
		except:
			pass
	else:
		pass

	return rsPackageDelivery

def make_derivs(ingestLogBoilerplate,processingVars,rsPackage=None):
	'''
	Make derivatives based on options declared in config...
	'''
	inputPath = processingVars['inputPath']
	packageObjectDir = processingVars['packageObjectDir']
	packageLogDir = processingVars['packageLogDir']
	packageMetadataObjects = processingVars['packageMetadataObjects']
	makeProres = processingVars['makeProres']
	ingestType = processingVars['ingestType']

	# make an enclosing folder for access copies if the input is a
	# group of related video files
	rsPackageDelivery = make_rs_package(processingVars['inputName'],rsPackage)

	# we'll always output a resourcespace access file for video ingests,
	# so init the derivtypes list with `resourcespace`
	if ingestType in ('film scan','video transfer'):
		derivTypes = ['resourcespace']
	
	# deliveredDerivPaths is a dict as follows:
	# {derivtype1:/path/to/deriv/file1}
	deliveredDerivPaths = {}
	
	if pymmFunctions.boolean_answer(
		config['deriv delivery options']['proresHQ']
		):
		derivTypes.append('proresHQ')
	elif makeProres == True:
		derivTypes.append('proresHQ')
	else:
		pass

	for derivType in derivTypes:
		sysargs = ['',
					'-i'+inputPath,
					'-o'+packageObjectDir,
					'-d'+derivType,
					'-L'+packageLogDir
					]
		if rsPackageDelivery != '':
			sysargs.append('-r'+rsPackageDelivery)
		sys.argv = 	sysargs
		
		deliveredDeriv = makeDerivs.main()
		deliveredDerivPaths[derivType] = deliveredDeriv

		event = 'migration'
		processingVars['caller'] = processingVars['ffmpeg']
		if os.path.exists(deliveredDeriv):
			outcome = 'create access copy at {}'.format(deliveredDeriv)
			status = 'OK'
		else:
			outcome = 'could not create access copy'
			status = 'FAIL'
		pymmFunctions.log_event(
			processingVars,
			ingestLogBoilerplate,
			event,
			outcome,
			status
			)
		processingVars['caller'] = None

	for key,value in deliveredDerivPaths.items():
		# metadata for each deriv is stored in a folder named
		# for the derivtype under the main Metadata folder
		mdDest = os.path.join(packageMetadataObjects,key)
		if not os.path.isdir(mdDest):
			os.mkdir(mdDest)
		origMDdest = processingVars['packageMetadataObjects']
		processingVars['packageMetadataObjects'] = mdDest
		processingVars['inputPath'] = value
		processingVars['filename'] = pymmFunctions.get_base(value)
		
		if os.path.isfile(value):
			processingVars = pymmFunctions.insert_object(
				processingVars,
				'file'
				)
			get_file_metadata(\
				ingestLogBoilerplate,\
				processingVars,\
				_type='derivative'\
				)
		else:
			pass

		if processingVars['pbcore'] != '':
			if derivType in ('resourcespace'):
				level = 'Access copy'
			elif derivType in ('proresHQ'):
				level = 'Mezzanine'
			else:
				level = 'Derivative'

			add_pbcore_instantiation(
				processingVars,
				ingestLogBoilerplate,
				level
				)
			# add_pbcore_md5_location(processingVars, fileMD5)

	# get a return value that is the path to the access copy(ies) delivered
	#   to a destination defined in config.ini
	# * for a single file it's the single deriv path
	# * for a folder of files it's the path to the enclosing deriv folder
	# 
	# this path is used to make an API call to resourcespace
	if rsPackageDelivery != '':
		accessPath = rsPackageDelivery
	else:
		SIPaccessPath = deliveredDerivPaths['resourcespace']
		deliveredAccessBase = os.path.basename(SIPaccessPath)
		rsOutDir = config['paths']['resourcespace_deliver']
		accessPath = os.path.join(rsOutDir,deliveredAccessBase)
	# reset metadata dir
	processingVars['packageMetadataObjects'] = origMDdest
	return accessPath

def stage_sip(processingVars,ingestLogBoilerplate):
	'''
	Move a prepped SIP to the AIP staging area.
	'''
	packageOutputDir = processingVars['packageOutputDir']
	aip_staging = processingVars['aip_staging']
	# tempID = processingVars['tempID']
	ingestUUID = processingVars['ingestUUID']
	sys.argv = 	['',
				'-i'+packageOutputDir,
				'-d'+aip_staging,
				'-L'+os.path.join(aip_staging,ingestUUID,'metadata','logs')]
	moveNcopy.main()

	# rename the staged dir
	stagedSIP = os.path.join(aip_staging,ingestUUID)
	# UUIDpath = os.path.join(aip_staging,ingestUUID)
	# pymmFunctions.rename_dir(stagedSIP,UUIDpath)
	processingVars['caller'] = 'rsync'
	pymmFunctions.short_log(
		processingVars,
		ingestLogBoilerplate,
		event = 'replication',
		outcome = 'SIP moved to staging area at {}'.format(stagedSIP),
		status = "OK"
		)
	processingVars['caller'] = None
	print(processingVars)

	return stagedSIP

def update_tempID(processingVars):
	'''
	replace filepath instances of temp ID with UUID
	to allow file opening
	'''
	tempID = processingVars['tempID']
	_uuid = processingVars['ingestUUID']
	for key, value in processingVars.items():
		if value and isinstance(value,str):
			if not key == 'tempID':
				if tempID in value:
					processingVars[key] = value.replace(tempID,_uuid)

		## @FIXME LOOK HERE FOR A WAY TO RECURSIVELY UPDATE NESTED DICT VALUES:
		# https://stackoverflow.com/questions/3232943/update-value-of-a-nested-dictionary-of-varying-depth
	return processingVars

def rename_SIP(processingVars,ingestLogBoilerplate):
	'''
	Rename the directory to the ingest UUID.
	'''
	pymmOutDir = config['paths']['outdir_ingestfile']
	packageOutputDir = processingVars['packageOutputDir']
	ingestUUID = processingVars['ingestUUID']
	UUIDpath = os.path.join(pymmOutDir,ingestUUID)
	# update the existing filepaths in processingVars & ingestLogBoilerplate
	processingVars = update_tempID(processingVars)
	ingestLogBoilerplate = update_tempID(ingestLogBoilerplate)
	# rename the SIP dir
	pymmFunctions.rename_dir(packageOutputDir,UUIDpath)
	processingVars['packageOutputDir'] = UUIDpath

	event = 'filename change'
	outcome = 'SIP renamed from {} to {}.'.format(
		packageOutputDir,
		UUIDpath
		)
	status = 'OK'
	processingVars['caller'] = 'pymmFunctions.rename_dir()'
	pymmFunctions.short_log(
		processingVars,
		ingestLogBoilerplate,
		event,
		outcome,
		status
		)
	processingVars['caller'] = None

	return processingVars,UUIDpath

def update_enveloped_paths(processingVars):
	'''
	update stored paths to reflect enveloped SIP structure
	'''
	pymmOutDir = config['paths']['outdir_ingestfile']
	_uuid = processingVars['ingestUUID']
	UUIDpath = os.path.join(pymmOutDir,_uuid)
	envelopedPath = os.path.join(UUIDpath,_uuid)
	for key, value in processingVars.items():
		if value and isinstance(value,str):
			if not key == 'packageOutputDir':
				if UUIDpath in value:
					processingVars[key] = value.replace(UUIDpath,envelopedPath)
	return processingVars

def envelop_SIP(processingVars,ingestLogBoilerplate):
	'''
	Make a parent directory named w UUID to facilitate hashdeeping/logging.
	Update the ingest log to reflect the new path.
	'''
	# processingVarsOrig,ingestLogBoilerplateOrig =\
	# 	processingVars,ingestLogBoilerplate
	ingestUUID = processingVars['ingestUUID']
	UUIDslice = ingestUUID[:8]
	pymmOutDir = config['paths']['outdir_ingestfile']
	_SIP = processingVars['packageOutputDir']
	event = 'faux-bag'
	outcome = (
		'Make a parent directory named w UUID '
		'to facilitate hash manifest/auditing.'
		)
	processingVars['caller'] = 'ingestSIP.envelop_SIP()'

	try:
		status = "OK"
		parentSlice = os.path.join(pymmOutDir,UUIDslice)
		# make a temp parent folder...
		os.mkdir(parentSlice)
		# ...move the SIP into it...
		shutil.move(_SIP,parentSlice)
		# ...and rename the parent w UUID path
		pymmFunctions.rename_dir(parentSlice,_SIP)
		processingVars = update_enveloped_paths(processingVars)
		ingestLogBoilerplate = update_enveloped_paths(ingestLogBoilerplate)
		print(processingVars)
	except:
		status = "FAIL"
		# reset the log path to its original state
		# ingestLogBoilerplate = ingestLogBoilerplateOrig
		print("Something no bueno.")

	pymmFunctions.short_log(
		processingVars,
		ingestLogBoilerplate,
		event,
		outcome,
		status
		)
	processingVars['caller'] = None

	return _SIP, ingestLogBoilerplate

def do_cleanup(\
	processingVars,\
	cleanupStrategy,\
	packageVerified,\
	inputPath,\
	packageOutputDir,\
	reason\
	):
	if cleanupStrategy == True and packageVerified == True:
		print("LET'S CLEEEEEAN!")
		pymmFunctions.cleanup_package(processingVars,inputPath,reason)
	else:
		print("BUH-BYE")

def directory_precheck(ingestLogBoilerplate,processingVars):
	'''
	Do some checking on directories:
	- remove system files
	- check for subdirectories
	'''
	precheckPass = (True,'')

	####################################
	### HUNT FOR HIDDEN SYSTEM FILES ###
	### DESTROY DESTROY DESTROY DEST ###

	removedFiles = pymmFunctions.remove_hidden_system_files(
		ingestLogBoilerplate['inputPath']
		)
	source_list = pymmFunctions.list_files(
		ingestLogBoilerplate['inputPath']
		)
	removeFailures = []
	status = "OK"
	event = "deletion"
	# check again for system files... just in case.
	for _object in source_list:
		if os.path.basename(_object).startswith('.'):
			try:
				removedFiles.append(_object)
				os.remove(_object)
			except:
				removeFailures.append(_object)
				print("tried to remove a pesky system file and failed.")
	if not removeFailures == []:
		if not removedFiles == []: 
			outcome = ("System files deleted at \n{}\n"
				"Some additional system files were NOT removed "
				"at \n{}\n".format(
					"\n".join(removedFiles),
					"\n".join(removeFailures)
					)
				)
			status == "INCONCLUSIVE"
		else:
			# if both passes failed, log it as such
			outcome = "Tried and failed to remove system files. Sorry."
			status = "FAIL"

		pymmFunctions.short_log(
			processingVars,
			ingestLogBoilerplate,
			event,
			outcome,
			status
			)
	else:
		if not removedFiles == []: 
			outcome = "System files deleted at \n{}\n".format(
				"\n".join(removedFiles)
				)
			pymmFunctions.short_log(
			processingVars,
			ingestLogBoilerplate,
			event,
			outcome,
			status
			)

		else:
			pass
	### END HIDDEN FILE HUNT ###
	############################

	subs = 0
	for _object in source_list:
		if os.path.isdir(_object):
			subs += 1
			print("\nYou have subdirectory(ies) in your input:"
				"\n({})\n".format(_object))
	if subs > 0:
		print("This is not currently supported. Exiting!")
		precheckPass = (False,'subdirectories in input')

	return precheckPass

def report_SIP_fixity(processingVars,objectManifestPath,eventID):
	parsed,hashes = pymmFunctions.parse_object_manifest(objectManifestPath)
	knownObjects = processingVars['componentObjectData']
	if not parsed == True:
		return parsed
	else:
		for _object, _hash in hashes.items():
			# hashes should look like {'filename':'md5hash'}
			processingVars['componentObjectData'][_object]['md5hash'] = _hash
			for key,value in knownObjects.items():
				if _object == key:
					processingVars['filename'] = _object
					pymmFunctions.insert_fixity(
						processingVars,
						eventID,
						messageDigestAlgorithm = "md5",
						messageDigestHashValue = _hash,
						messageDigestSource=objectManifestPath
						)
	return processingVars

# def stash_manifest(manifestPath):
# 	'''
# 	rename a manifest as _old_ and stash it in the metadata directory
# 	(to be run before making a new/final hashdeep manifest)
# 	'''

def main():
	#########################
	#### SET INGEST ARGS ####
	args = set_args()
	inputPath = args.inputPath
	operator = args.operator
	objectBAMPFAjson = args.metadataJSON
	database_reporting = args.database_reporting
	ingestType = args.ingestType
	makeProres = args.makeProres
	concatChoice = args.concatAccessFiles
	cleanupStrategy = args.cleanup_originals
	interactiveMode = args.interactiveMode
	# read aip staging dir from config
	aip_staging = config['paths']['aip_staging']

	# make a uuid for the ingest
	ingestUUID = str(uuid.uuid4())
	# make a temp ID based on input path for the ingested object
	# this will get replaced by the ingest UUID during final package move
	tempID = pymmFunctions.get_temp_id(inputPath)
	#### END SET INGEST ARGS #### 
	#############################

	#############################
	#### TEST / SET ENV VARS ####
	# get the name of the local machine to record w/ PREMIS data
	computer = pymmFunctions.get_node_name()
	# get the version of ffmpeg in use
	ffmpegVersion = 'ffmpeg ver.: '+pymmFunctions.get_ffmpeg_version()
	# init a dict of outcomes to be returned
	ingestResults = {
		'status':False,
		'abortReason':'',
		'ingestUUID':''
		}
	# sniff whether the input is a file or directory
	inputType = sniff_input(inputPath,ingestUUID)
	if not inputType:
		print(ingestResults)
		return ingestResults
	try:
		# create directory paths for ingest...
		packageOutputDir,packageObjectDir,packageMetadataDir,\
		packageMetadataObjects,packageLogDir = prep_package(tempID)
	except:
		ingestResults["abortReason"] = (
			"package previously ingested, remove manually"
			)
		print(ingestResults)
		return ingestResults

	# check that required vars are declared & init other vars
	requiredVars = ['inputPath','operator']
	if interactiveMode == False:
		# Quit if there are required variables missing
		missingVars = 0
		missingVarsReport = ""
		for flag in requiredVars:
			if getattr(args,flag) == None:
				problem = ('''
					CONFIGURATION PROBLEM:
					YOU FORGOT TO SET '''+flag+'''. It is required.
					Try again, but set '''+flag+''' with the flag --'''+flag
					)
				missingVars += 1
				missingVarsReport += "\n{}\n".format(problem)
				print(problem)
		if missingVars > 0:
			ingestResults["abortReason"] = (
				"ingestSip.py called with some flags missing."
				)
			pymmFunctions.pymm_log(
				processingVars,
				event = 'abort',
				outcome = missingVarsReport,
				status = 'ABORTING'
				)
			print(ingestResults)
			return ingestResults
	else:
		# ask operator/input file
		operator = input("Please enter your name: ")
		inputPath = input("Please drag the file you want to ingest into this window___").rstrip()
		inputPath = pymmFunctions.sanitize_dragged_linux_paths(inputPath)

	# get database details
	if database_reporting != False:
		pymmDB = config['database settings']['pymm_db']
		# insert a db connection test here instead of later down in main()
		if not operator in config['database users']:
			# SHOULD THIS CAUSE AN EXIT(1)?
			# @fixme
			print(
				"{} is not a valid user in the pymm database."
				"".format(operator)
				)
			# idea: set database_reporting to False
	# Set up a canonical name that will be passed to each log entry.
	# For files it's the basename, for dirs it's the dir name.
	if inputPath:
		canonicalName = os.path.basename(inputPath)
		if inputType == 'file':
			filename = inputName = canonicalName
		elif inputType == 'dir':
			filename = ''
			inputName = canonicalName

	# set up a dict for processing variables to pass around
	processingVars = {
		'operator':operator,
		'inputPath':inputPath,
		'objectBAMPFAjson':objectBAMPFAjson,
		'pbcore':'',
		'tempID':tempID,
		'ingestType':ingestType,
		'ingestUUID':ingestUUID,
		'filename':filename,
		'inputName':inputName,
		'makeProres':makeProres,
		'packageOutputDir':packageOutputDir,
		'packageObjectDir':packageObjectDir,
		'packageMetadataDir':packageMetadataDir,
		'packageMetadataObjects':packageMetadataObjects,
		'packageLogDir':packageLogDir,
		'aip_staging':aip_staging,
		'componentObjectData':{},
		'computer':computer,
		'caller':None,
		'database_reporting':database_reporting,
		'ffmpeg':ffmpegVersion
		}
	#### END TEST / SET ENV VARS ####
	#################################

	###########################
	#### LOGGING / CLEANUP ####
	# set up a log file for this ingest...
	ingestLogPath = os.path.join(
		packageLogDir,
		'{}_{}_ingestfile-log.txt'.format(
			tempID,
			pymmFunctions.timestamp('now')
			)
		)
	with open(ingestLogPath,'x') as ingestLog:
		print('Laying a log at '+ingestLogPath)
	ingestLogBoilerplate = {
		'ingestLogPath':ingestLogPath,
		'tempID':tempID,
		'inputName':inputName,
		'filename':filename,
		'operator':operator,
		'inputPath':inputPath,
		'ingestUUID':ingestUUID
		}
	# insert a database record for this SIP as an 'intellectual entity'
	origFilename = processingVars['filename']
	processingVars['filename'] = ingestUUID
	processingVars = pymmFunctions.insert_object(
		processingVars,
		'intellectual entity'
		)
	# tell the various logs that we are starting
	processingVars['caller'] = 'ingestSIP.main()'
	pymmFunctions.log_event(
		processingVars,
		ingestLogBoilerplate,
		event = 'ingestion start',
		outcome = "SYSTEM INFO:\n{}".format(pymmFunctions.system_info()),
		status = 'STARTING'
		)
	# reset variables
	processingVars['caller'] = None

	# if interactive ask about cleanup
	if interactiveMode:
		reset_cleanup_choice()

	# RUN A PRECHECK ON DIRECTORY INPUTS
	if inputType == 'dir':
		precheckPass,precheckFailReason = directory_precheck(
			ingestLogBoilerplate,
			processingVars
			)
		if precheckPass == False:
			pymmFunctions.cleanup_package(
				processingVars,
				packageOutputDir,
				"ABORTING",
				precheckFailReason
				)
			ingestResults['abortReason'] = precheckFailReason
			print(ingestResults)
			return ingestResults
		else:
			source_list = pymmFunctions.list_files(inputPath)

	# create a PBCore XML file and send any existing BAMPFA metadata JSON
	# to the object metadata directory.
	# We will add instantiation data later during ingest
	pbcoreXML = pbcore.PBCoreDocument()
	# NOTE TO SELF: 
	# REALLY I SHOULD SEPARATE THE BAMPFA COLLECTION JSON
	# FROM WHATEVER THE USER-DEFINED/CREATED DESCRIPTIVE METADATA JSON 
	# THAT WILL EVENTUALLY EXIST.
	# SO, 
	# if objectBAMPFAjson != None:
	#	do stuff
	# elif descriptiveJSON != None:
	#	do stuff
	# else:
	# 	do stuff
	if objectBAMPFAjson != None:
		# move it
		copy = shutil.copy2(
			objectBAMPFAjson,
			processingVars['packageMetadataDir']
			)
		# reset var to new path
		processingVars['objectBAMPFAjson'] = os.path.abspath(copy)
		makePbcore.add_physical_elements(
			pbcoreXML,
			processingVars['objectBAMPFAjson']
			)
		pbcoreFile = makePbcore.xml_to_file(
			pbcoreXML,
			os.path.join(
				processingVars['packageMetadataDir'],
				canonicalName+"_pbcore.xml"
				)
			)
	else:
		# if no bampfa metadata, just make a container pbcore.xml w/o a
		# representation of the physical asset
		pbcoreFile = makePbcore.xml_to_file(
			pbcoreXML,
			os.path.join(
				processingVars['packageMetadataDir'],
				canonicalName+"_pbcore.xml"
				)
			)

	processingVars['pbcore'] = pbcoreFile
	if os.path.exists(pbcoreFile):
		status = 'OK'
	else:
		status = 'Fail'
	processingVars['caller'] = 'pbcore.PBCoreDocument() , makePbcore.xml_to_file()'
	pymmFunctions.short_log(
		processingVars,
		ingestLogBoilerplate,
		event = 'metadata extraction',
		outcome = 'make pbcore representation',
		status = status
		)
	processingVars['caller'] = None
	processingVars['filename'] = origFilename

	#### END LOGGING / CLEANUP ####
	###############################

	###############
	## DO STUFF! ##
	###############
	if inputType == 'file':
		if database_reporting:
			try:
				processingVars = pymmFunctions.insert_object(
					processingVars,
					objectCategory = 'file'
					)
			except:
				print("CAN'T MAKE DB CONNECTION")
				pymmFunctions.pymm_log(
					processingVars,
					event = "connect to database",
					outcome = "NO DATABASE CONNECTION!!!",
					status = "WARNING"
					)
		# check that input file is actually a/v
		# THIS CHECK SHOULD BE AT THE START OF THE INGEST PROCESS
		avStatus = check_av_status(
			inputPath,
			interactiveMode,
			ingestLogBoilerplate,
			processingVars
			)
		# mediaconch_check(inputPath,ingestType,ingestLogBoilerplate) # @dbme
		move_input_file(processingVars,ingestLogBoilerplate)

		add_pbcore_instantiation(
			processingVars,
			ingestLogBoilerplate,
			"Preservation master"
			)

		get_file_metadata(ingestLogBoilerplate,processingVars)
		pymmFunctions.pymm_log(
			processingVars,
			event = 'metadata extraction',
			outcome = 'calculate input file technical metadata',
			status = 'OK'
			)
		accessPath = make_derivs(ingestLogBoilerplate,processingVars)

	elif inputType == 'dir':
		if database_reporting:
			try:
				# print(processingVars)
				processingVars = pymmFunctions.insert_object(
					processingVars,
					objectCategory = 'intellectual entity'
					)
			except:
				print("CAN'T MAKE DB CONNECTION")
				pymmFunctions.pymm_log(
					processingVars,
					event = "connect to database",
					outcome = "NO DATABASE CONNECTION!!!",
					status = "WARNING"
					)
		for _file in source_list:			
			# set processing variables per file 
			ingestLogBoilerplate['filename'] = os.path.basename(_file)
			processingVars['filename'] = os.path.basename(_file)
			processingVars['inputPath']=\
				ingestLogBoilerplate['inputPath'] = _file
			if database_reporting:
				try:
					processingVars = pymmFunctions.insert_object(
						processingVars,
						objectCategory = 'file'
						)
				except:
					print("CAN'T MAKE DB CONNECTION")
					pymmFunctions.pymm_log(
						processingVars,
						event = "connect to database",
						outcome = "NO DATABASE CONNECTION!!!",
						status = "WARNING"
						)
			#######################
			# check that input file is actually a/v
			# THIS CHECK SHOULD BE AT THE START OF THE INGEST PROCESS
			check_av_status(
				_file,
				interactiveMode,
				ingestLogBoilerplate,
				processingVars
				)
			# check against mediaconch policy
			# mediaconch_check(_file,ingestType,ingestLogBoilerplate) # @dbme

			move_input_file(processingVars,ingestLogBoilerplate)

			add_pbcore_instantiation(
				processingVars,
				ingestLogBoilerplate,
				"Preservation master"
				)

			get_file_metadata(ingestLogBoilerplate,processingVars)
			pymmFunctions.pymm_log(
				processingVars,
				event = 'metadata extraction',
				outcome = 'calculate input file technical metadata',
				status = 'OK'
				)
			#######################
			# for a directory input, accessPath is 
			# the containing folder under the one
			# defined in config.ini
			accessPath = make_derivs(
				ingestLogBoilerplate,
				processingVars,
				rsPackage=True
				)
			
		# reset the processing variables to the original state 
		processingVars['filename'] = ''
		processingVars['inputPath']=\
			processingVars['inputName'] =\
			ingestLogBoilerplate['inputPath'] = inputPath

		if concatChoice == True:
			# TRY TO CONCATENATE THE ACCESS FILES INTO A SINGLE FILE...
			# @dbme
			SIPaccessPath = os.path.join(
				processingVars['packageObjectDir'],
				'resourcespace'
				)
			concatPath = concat_access_files(
				SIPaccessPath,
				ingestUUID,
				canonicalName,
				'mp4',
				ingestLogBoilerplate,
				processingVars
				)
			if os.path.exists(concatPath):
				deliver_concat_access(
					concatPath,
					accessPath
					)

	#########
	# FLUFF THE SIP FOR STAGING
	# set the logging 'filename' to the UUID for the rest of the process
	processingVars['filename'] = ingestUUID
	# rename SIP from temp to UUID
	processingVars,_SIP = rename_SIP(processingVars,ingestLogBoilerplate)
	# put the package into a UUID parent folder
	_SIP,ingestLogBoilerplate = envelop_SIP(
		processingVars,
		ingestLogBoilerplate
		)
	###  MAKE A NEW FUNCTION TO CONTAIN THIS AND THE SIP MANIFEST CREATION INSTEAD
	###  OF DOING THEM SEPARATELY/ D.R.Y.
	objectManifestPath = makeMetadata.make_hashdeep_manifest(
		_SIP,
		'objects'
		)
	outcome = (
		'create hashdeep manifest '
		'for objects directory at {}'.format(objectManifestPath)
		)
	status = 'OK'
	processingVars['caller'] = 'hashdeep'
	if not os.path.isfile(objectManifestPath):
		status = 'FAIL'
		outcome = 'could not '+outcome
	else:
		pass
	eventID = pymmFunctions.short_log(
		processingVars,
		ingestLogBoilerplate,
		event = 'message digest calculation',
		outcome = outcome,
		status = status
		)
	# report data from the object manifest
	# get back updated processingVars['componentObjectData'] dict
	processingVars = report_SIP_fixity(processingVars,objectManifestPath,eventID)
	processingVars['caller'] = None	
	# also add md5 and filename for each object as identifiers
	# to the pbcore record
	add_pbcore_md5_location(processingVars)
	
	#####
	# AT THIS POINT THE SIP IS FULLY FORMED SO LOG IT AS SUCH
	processingVars['caller'] = 'ingestSIP.main()'
	pymmFunctions.short_log(
		processingVars,
		ingestLogBoilerplate,
		event = 'information package creation',
		outcome = 'assemble SIP into valid structure',
		status = 'OK'
		)
	processingVars['caller'] = None

	# recursively set SIP and manifest to 777 file permission
	chmodded = pymmFunctions.recursive_chmod(_SIP)
	processingVars['caller'] = 'Python3 os.chmod(x,0o777)'
	pymmFunctions.short_log(
		processingVars,
		ingestLogBoilerplate,
		event = 'modification',
		outcome = 'recursively set SIP and manifest file permissions to 777',
		status = 'OK'
		)
	processingVars['caller'] = None

	packageVerified = False
	# move the SIP if needed
	if not aip_staging == config['paths']['outdir_ingestfile']:
		_SIP = stage_sip(processingVars, ingestLogBoilerplate)
		# audit the hashdeep manifest 
		objectsVerified = False
		auditOutcome,objectsVerified = makeMetadata.hashdeep_audit(
			_SIP,
			objectManifestPath,
			_type='objects'
			)
		processingVars['caller'] = 'hashdeep'
		if objectsVerified == True:
			status = 'OK'
			outcome = (
				'Objects directory verified against hashdeep manifest: \n{}'
				''.format(auditOutcome)
				)
		else:
			status = "WARNING"
			outcome = (
				"Objects directory failed hashdeep audit. "
				"Some files may be missing or damaged!"
				"Check the hashdeep audit: \n{}".format(auditOutcome)
				)
		pymmFunctions.log_event(
			processingVars,
			ingestLogBoilerplate,
			event = 'fixity check',
			outcome = outcome,
			status = status
			)
		processingVars['caller'] = None

	else:
		objectsVerified = True

	validSIP,validationOutcome = pymmFunctions.validate_SIP_structure(_SIP)
	if not validSIP:
		# IS THIS EXCESSIVE?? MAYBE JUST LOG 
		#     THAT IT DIDN"T PASS MUSTER BUT SAVE IT.
		# OR MAKE AN "INCONCLUSIVE/WARNING" VERSION?
		# NOTE: the failure gets logged in the system log,
		# along with listing reasons for failure
		# then the abort message is logged in cleanup_package()
		pymmFunctions.cleanup_package(
			processingVars,
			_SIP,
			"ABORTING",
			validationOutcome
			)
		return ingestResults
	else:
		# set inputPath as the SIP path
		processingVars['inputPath'] = _SIP
		processingVars['caller'] = 'pymmFunctions.validate_SIP_structure()'
		pymmFunctions.log_event(
			processingVars,
			ingestLogBoilerplate,
			event = "validation",
			outcome = "SIP validated against expected structure",
			status = "OK"
			)
		processingVars['caller'] = None

	if objectsVerified and validSIP:
		ingestResults['status'] = True
	ingestResults['ingestUUID'] = ingestUUID
	ingestResults['accessPath'] = accessPath

	# add_pbcore_md5_location(processingVars)

	# THIS IS THE LAST CALL MADE TO MODIFY ANYTHING IN THE SIP.
	pymmFunctions.ingest_log(
		event = 'ingestion end',
		outcome = 'Submission Information Package verified and staged',
		status = 'ENDING',
		**ingestLogBoilerplate
		)

	# make a hashdeep manifest
	manifestPath = makeMetadata.make_hashdeep_manifest(
		_SIP,
		'hashdeep'
		)
	processingVars['caller'] = 'hashdeep'
	if os.path.isfile(manifestPath):
		pymmFunctions.end_log(
			processingVars,
			event = 'message digest calculation',
			outcome = 'create hashdeep manifest for SIP at {}'.format(manifestPath),
			status = 'OK'
			)
	processingVars['caller'] = 'ingestSIP.main()'	
	pymmFunctions.end_log(
		processingVars,
		event = 'ingestion end',
		outcome = 'Submission Information Package verified and staged',
		status = 'ENDING'
		)
	# print(processingVars)

	do_cleanup(
		processingVars,
		cleanupStrategy,
		objectsVerified,
		inputPath,
		packageOutputDir,
		'done'
		)

	print(ingestResults)
	return ingestResults

if __name__ == '__main__':
	main()
