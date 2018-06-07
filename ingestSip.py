#!/usr/bin/env python3
'''
`ingestSip` takes an input a/v file or directory of a/v files,
transcodes a derivative for each file,
produces/extracts some metadata,
creates fixity checks,
and packages the whole lot in an OAIS-like Archival Information Package

@fixme = stuff to do
'''
# standard library modules
import argparse
import json
import os
import shutil
import subprocess
import sys
import uuid
# local modules:
import pymmFunctions
import makeDerivs
import moveNcopy
import makeMetadata
import concatFiles
import premisSQL

from bampfa_pbcore import pbcore, makePbcore

# read in from the config file
config = pymmFunctions.read_config()
# check that paths required for ingest are declared in config.ini
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
		sys.exit(1)

	# ... AND IF NOT, MAKE THEM ALL
	for directory in packageDirs:
		os.mkdir(directory)

	return packageDirs

def sniff_input(inputPath,ingestUUID):#,concatChoice):
	'''
	Check whether the input path from command line is a directory
	or single file. If it's a directory, check that the filenames
	make sense together or if there are any outliers.
	'''
	inputType = pymmFunctions.dir_or_file(inputPath)
	if inputType == 'dir':
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

def concat_access_files(inputPath,ingestUUID,canonicalName,wrapper):
	concattedAccessFile = False

	sys.argv = [
		'',
		'-i'+inputPath,
		'-d'+ingestUUID,
		'-c'+canonicalName,
		'-w'+wrapper
		]
	try:
		concattedAccessFile = concatFiles.main()
	except:
		print('couldnt concat files')

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

def check_av_status(inputPath,interactiveMode,ingestLogBoilerplate):
	'''
	Check whether or not a file is recognized as an a/v file.
	If it isn't and user declares interactive mode, ask whether to continue, otherwise quit.
	'''
	if not pymmFunctions.is_av(inputPath):
		_is_av = False
		message = "WARNING: {} is not recognized as an a/v file.".format(
			ingestLogBoilerplate['filename']
			)
		print(message)
		pymmFunctions.ingest_log(
			# message
			message,
			#status
			'warning',
			# ingest boilerplate
			**ingestLogBoilerplate
			)

	if interactiveMode:
		stayOrGo = input("If you want to quit press 'q' and hit enter, otherwise press any other key:")
		if stayOrGo == 'q':
			# CLEANUP AND LOG THIS @fixme
			sys.exit()
		else:
			if _is_av == False:
				pymmFunctions.ingest_log(
					# message
					message,
					# status
					'warning',
					# ingest boilerplate
					**ingestLogBoilerplate
					)
	else:
		pymmFunctions.ingest_log(
			# message
			ingestLogBoilerplate['filename']+" is an AV file, way to go.",
			# status
			'OK',
			# ingest boilerplate
			**ingestLogBoilerplate
			)

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
			# message
			message,
			# status
			status,
			# ingest boilerplate
			**ingestLogBoilerplate
			)

def move_input_file(processingVars):
	'''
	Put the input file into the package object dir.
	'''
	sys.argv = [
		'',
		'-i'+processingVars['inputPath'],
		'-d'+processingVars['packageObjectDir'],
		'-L'+processingVars['packageLogDir']
		]
	moveNcopy.main()

def input_file_metadata(ingestLogBoilerplate,processingVars):
	inputFile = processingVars['inputPath']
	inputFileMD5 = makeMetadata.hash_file(inputFile)
	
	pymmFunctions.ingest_log(
		# message
		"The input file MD5 hash is: {}".format(
			inputFileMD5
			),
		# status
		'OK',
		# ingest boilerplate
		**ingestLogBoilerplate
		)
	mediainfo = makeMetadata.get_mediainfo_report(
		processingVars['inputPath'],
		processingVars['packageMetadataObjects']
		)
	if mediainfo:
		pymmFunctions.ingest_log(
			# message
			("mediainfo XML report for input file "
			"written to metadata directory for package."),
			# status
			'OK',
			# ingest boilerplate
			**ingestLogBoilerplate
			)
	
	frameMD5 = makeMetadata.make_frame_md5(
		processingVars['inputPath'],
		processingVars['packageMetadataObjects']
		)
	if frameMD5 != False:
		pymmFunctions.ingest_log(
			# message
			("frameMD5 report for input file "
			"written to metadata directory for package"),
			# status
			"OK",
			# ingest boilerplate
			**ingestLogBoilerplate
			)

def add_pbcore_md5_location(processingVars, inputFileMD5):
	if processingVars['pbcore'] != '':
		pbcoreFile = processingVars['pbcore']
		pbcoreXML = pbcore.PBCoreDocument(pbcoreFile)
		# add md5 as an identifier to the pbcoreInstantiation for the file
		attributes = {
			"source":"BAMPFA {}".format(pymmFunctions.timestamp()),
			"annotation":"messageDigest",
			"version":"MD5"
		}
		# print(attributes)
		makePbcore.add_element_to_instantiation(
			pbcoreXML,
			processingVars['filename'],
			'instantiationIdentifier',
			attributes,
			inputFileMD5
			)
		# add 'BAMPFA Digital Repository' as instantiationLocation
		attributes = {}
		makePbcore.add_element_to_instantiation(
			pbcoreXML,
			processingVars['filename'],
			'instantiationLocation',
			attributes,
			"BAMPFA Digital Repository"
			)
		makePbcore.xml_to_file(
			pbcoreXML,
			pbcoreFile
			)

def add_pbcore_instantiation(processingVars,level):
	_file = processingVars['inputPath']
	pbcoreReport = makeMetadata.get_mediainfo_pbcore(_file)
	# print(pbcoreReport)
	descriptiveJSONpath = processingVars['objectBAMPFAjson']
	pbcoreFile = processingVars['pbcore']
	pbcoreXML = pbcore.PBCoreDocument(pbcoreFile)

	makePbcore.add_instantiation(
		pbcoreXML,
		pbcoreReport,
		descriptiveJSONpath=descriptiveJSONpath,
		level=level
		)
	makePbcore.xml_to_file(pbcoreXML,pbcoreFile)

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
	rsPackageDelivery = make_rs_package(processingVars['input_name'],rsPackage)

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

	for key,value in deliveredDerivPaths.items():
		# metadata for each deriv is stored in a folder named
		# for the derivtype under the main Metadata folder
		mdDest = os.path.join(packageMetadataObjects,key)
		if not os.path.isdir(mdDest):
			os.mkdir(mdDest)
		mediainfo = makeMetadata.get_mediainfo_report(value,mdDest)

		if processingVars['pbcore'] != '':
			if derivType in ('resourcespace'):
				level = 'Access copy'
			elif derivType in ('proresHQ'):
				level = 'Mezzanine'
			else:
				level = 'Derivative'
			# basename = pymmFunctions.get_base(value)
			processingVars['inputPath'] = value
			processingVars['filename'] = pymmFunctions.get_base(value)
			# print(processingVars)
			fileMD5 = makeMetadata.hash_file(value)
			add_pbcore_instantiation(processingVars, level)
			add_pbcore_md5_location(processingVars, fileMD5)

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
	return accessPath

def stage_sip(processingVars):
	'''
	Move a prepped SIP to the AIP staging area.
	'''
	packageOutputDir = processingVars['packageOutputDir']
	aip_staging = processingVars['aip_staging']
	tempID = processingVars['tempID']
	ingestUUID = processingVars['ingestUUID']
	sys.argv = 	['',
				'-i'+packageOutputDir,
				'-d'+aip_staging,
				'-L'+os.path.join(aip_staging,ingestUUID)]
	moveNcopy.main()
	# rename the staged dir
	stagedSIP = os.path.join(aip_staging,ingestUUID)
	# UUIDpath = os.path.join(aip_staging,ingestUUID)
	# pymmFunctions.rename_dir(stagedSIP,UUIDpath)

	return stagedSIP

def rename_SIP(processingVars):
	'''
	Rename the directory to the ingest UUID.
	'''
	pymmOutDir = config['paths']['outdir_ingestfile']
	packageOutputDir = processingVars['packageOutputDir']
	ingestUUID = processingVars['ingestUUID']
	UUIDpath = os.path.join(pymmOutDir,ingestUUID)
	pymmFunctions.rename_dir(packageOutputDir,UUIDpath)
	processingVars['packageOutputDir'] = UUIDpath

	return processingVars,UUIDpath

def envelop_SIP(processingVars):
	'''
	Make a parent directory named w UUID to facilitate hashdeeping/logging.
	'''
	ingestUUID = processingVars['ingestUUID']
	UUIDslice = ingestUUID[:8]
	pymmOutDir = config['paths']['outdir_ingestfile']
	_SIP = processingVars['packageOutputDir']
	try:
		parentSlice = os.path.join(pymmOutDir,UUIDslice)
		# make a temp parent folder...
		os.mkdir(parentSlice)
		# ...move the SIP into it...
		shutil.move(_SIP,parentSlice)
		# ...and rename the parent w UUID path
		pymmFunctions.rename_dir(parentSlice,_SIP)
	except:
		print("Something no bueno.")

	return _SIP

def do_cleanup(cleanupStrategy,packageVerified,inputPath,packageOutputDir,reason):
	if cleanupStrategy == True and packageVerified == True:
		print("LET'S CLEEEEEAN!")
		pymmFunctions.cleanup_package(inputPath,packageOutputDir,reason)
	else:
		print("BUH-BYE")

def main():
	#########################
	#### SET INGEST ARGS ####
	args = set_args()
	inputPath = args.inputPath
	operator = args.operator
	objectBAMPFAjson = args.metadataJSON
	report_to_db = args.database_reporting
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
	# init a dict of outcomes to be returned
	ingestReults = {
		'status':False,
		'UUID':''
	}
	# sniff whether the input is a file or directory
	inputType = sniff_input(inputPath,ingestUUID)#,concatChoice)
	if not inputType:
		sys.exit(1)
	if inputType == 'dir':
		# REMOVE SYSTEM FILES
		# @logme
		pymmFunctions.remove_hidden_system_files(inputPath)
		source_list = pymmFunctions.list_files(inputPath)
		subs = 0
		for _object in source_list:
			if os.path.isdir(_object):
				subs += 1
				print("\nYou have subdirectory(ies) in your input:"
					"\n({})\n".format(_object))
		if subs > 0:
			print("This is not currently supported. Exiting!")
			sys.exit(1)

	# create directory paths for ingest...
	packageOutputDir,packageObjectDir,packageMetadataDir,\
	packageMetadataObjects,packageLogDir = prep_package(tempID)

	# check that required vars are declared & init other vars
	requiredVars = ['inputPath','operator']
	if interactiveMode == False:
		# Quit if there are required variables missing
		missingVars = 0
		for flag in requiredVars:
			if getattr(args,flag) == None:
				print('''
					CONFIGURATION PROBLEM:
					YOU FORGOT TO SET '''+flag+'''. It is required.
					Try again, but set '''+flag+''' with the flag --'''+flag
					)
				missingVars += 1
		if missingVars > 0:
			sys.exit()
	else:
		# ask operator/input file
		operator = input("Please enter your name: ")
		inputPath = input("Please drag the file you want to ingest into this window___").rstrip()
		inputPath = pymmFunctions.sanitize_dragged_linux_paths(inputPath)

	# get database details
	if report_to_db != None:
		pymmDB = config['database settings']['pymm_db']
		if not operator in config['database users']:
			print("{} is not a valid user in the pymm database.".format(operator))

	# Set up a canonical name that will be passed to each log entry.
	# For files it's the basename, for dirs it's the dir name.
	if inputPath:
		canonicalName = os.path.basename(inputPath)
		if inputType == 'file':
			filename = input_name = canonicalName
		elif inputType == 'dir':
			filename = ''
			input_name = canonicalName

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
		'input_name':input_name,
		'makeProres':makeProres,
		'packageOutputDir':packageOutputDir,
		'packageObjectDir':packageObjectDir,
		'packageMetadataDir':packageMetadataDir,
		'packageMetadataObjects':packageMetadataObjects,
		'packageLogDir':packageLogDir,
		'aip_staging':aip_staging
		}
	#### END TEST / SET ENV VARS ####
	#################################

	###########################
	#### LOGGING / CLEANUP ####
	# set up a log file for this ingest
	ingestLogPath = os.path.join(
		packageLogDir,
		'{}_{}_ingestfile-log.txt'.format(
			tempID,pymmFunctions.timestamp('now')
			)
		)
	with open(ingestLogPath,'x') as ingestLog:
		print('Laying a log at '+ingestLogPath)
	ingestLogBoilerplate = {
		'ingestLogPath':ingestLogPath,
		'tempID':tempID,
		'input_name':input_name,
		'filename':filename,
		'operator':operator
		}
	pymmFunctions.ingest_log(
		# message
		'start',
		# status
		'start',
		# ingest boilerplate
		**ingestLogBoilerplate
		)

	# tell the system log that we are starting
	pymmFunctions.pymm_log(input_name,tempID,operator,'','STARTING')

	# if interactive ask about cleanup
	if interactiveMode:
		reset_cleanup_choice()

	# insert database record for this ingest (log 'ingestion start') 
	# --> http://id.loc.gov/vocabulary/preservation/eventType/ins.html
	# @fixme
	# @logme # @dbme


	# create a PBCore XML file and send any existing BAMPFA metadata JSON
	# to the object metadata directory.
	pbcoreXML = pbcore.PBCoreDocument()
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
		processingVars['pbcore'] = pbcoreFile

	else:
		# if no bampfa metadata, just make a pbcore.xml w/o a
		# representation of the physical asset
		pbcoreFile = makePbcore.xml_to_file(
			pbcoreXML,
			os.path.join(
				processingVars['packageMetadataDir'],
				canonicalName+"_pbcore.xml"
				)
			)
		processingVars['pbcore'] = pbcoreFile

	#### END LOGGING / CLEANUP ####
	###############################

	###############
	## DO STUFF! ##
	###############
	if inputType == 'file':
		if report_to_db:
			objectCategory = 'file'
			try:
				objectIdentifierValueID = pymmFunctions.insert_object(
					operator,
					canonicalName,
					objectCategory
					)
			except:
				print("CAN'T MAKE DB CONNECTION")
				pymmFunctions.pym_log(
					input_name,
					tempID,
					operator,
					"NO DATABASE CONNECTION!!!",
					"WARNING"
					)
		# check that input file is actually a/v
		# THIS CHECK SHOULD BE AT THE START OF THE INGEST PROCESS
		check_av_status(inputPath,interactiveMode,ingestLogBoilerplate) # @dbmemediaconch_check(inputPath,ingestType,ingestLogBoilerplate) # @dbme
		move_input_file(processingVars) # @logme # @dbme
		add_pbcore_instantiation(
			processingVars,
			"Preservation master"
			) # @dbme
		input_file_metadata(ingestLogBoilerplate,processingVars) # @logme # @dbme
		accessPath = make_derivs(ingestLogBoilerplate,processingVars) # @logme # @dbme
	elif inputType == 'dir':
		if report_to_db:
			objectCategory = 'intellectual entity'
			try:
				objectIdentifierValueID = pymmFunctions.insert_object(
					operator,
					canonicalName,
					objectCategory
					)
			except:
				print("CAN'T MAKE DB CONNECTION")
				pymmFunctions.pym_log(
					input_name,
					tempID,
					operator,
					"NO DATABASE CONNECTION!!!",
					"WARNING"
					)
		for _file in source_list:
			
			# set processing variables per file 
			ingestLogBoilerplate['filename'] = os.path.basename(_file) # @dbme
			processingVars['filename'] = os.path.basename(_file) # @dbme
			processingVars['inputPath'] = _file # @dbme
			if report_to_db:
				objectCategory = 'file'
				try:
					objectIdentifierValueID = pymmFunctions.insert_object(
						operator,
						processingVars['filename'],
						objectCategory
						)
				except:
					print("CAN'T MAKE DB CONNECTION")
					pymmFunctions.pym_log(
						input_name,
						tempID,
						operator,
						"NO DATABASE CONNECTION!!!",
						"WARNING"
						)
			# check that input file is actually a/v
			# THIS CHECK SHOULD BE AT THE START OF THE INGEST PROCESS
			check_av_status(_file,interactiveMode,ingestLogBoilerplate) # @dbme
			mediaconch_check(_file,ingestType,ingestLogBoilerplate) # @dbme
			move_input_file(processingVars) # @dbme
			add_pbcore_instantiation(
				processingVars,
				"Preservation master"
				) # @dbme
			input_file_metadata(ingestLogBoilerplate,processingVars) # @dbme 
			# for a directory input, accessPath is 
			# the containing folder under the one
			# defined in config.ini
			accessPath = make_derivs(
					ingestLogBoilerplate,
					processingVars,
					rsPackage=True
				) # @dbme
		# reset the processing variables to the original state 
		processingVars['filename'] = ''
		processingVars['inputPath'] = inputPath

		if concatChoice == True:
			# TRY TO CONCATENATE THE ACCESS FILES INTO A SINGLE FILE...
			# @logme @dbme
			SIPaccessPath = os.path.join(
				processingVars['packageObjectDir'],
				'resourcespace'
				)
			concatPath = concat_access_files(
				SIPaccessPath,
				ingestUUID,
				canonicalName,
				'mp4'
				)
			if not concatPath == False:
				deliver_concat_access(
					concatPath,
					accessPath
					)

	#########
	# MOVE SIP TO AIP STAGING
	# rename SIP from temp to UUID
	processingVars,SIPpath = rename_SIP(processingVars) # @dbme
	# put the package into a UUID parent folder
	_SIP = envelop_SIP(processingVars) # @dbme
	# make a hashdeep manifest
	manifestPath = makeMetadata.make_hashdeep_manifest(
		_SIP
		) # @dbme
	# recursively set SIP and manifest to 777 file permission
	chmodded = pymmFunctions.recursive_chmod(_SIP)
	# move the SIP if needed
	packageVerified = False
	if not aip_staging == config['paths']['outdir_ingestfile']:
		_SIP = stage_sip(processingVars) # @dbme
		# c) audit the hashdeep manifest 
		# packageVerified = result of audit
		packageVerified = makeMetadata.hashdeep_audit(
			_SIP,
			manifestPath
			) # @dbme
	else:
		# probably pointless on same filesystem
		packageVerified = makeMetadata.hashdeep_audit(
			_SIP,
			manifestPath
			) # @dbme

	# FINISH LOGGING
	do_cleanup(cleanupStrategy,packageVerified,inputPath,packageOutputDir,'done') # @dbme

	if packageVerified:
		ingestReults['status'] = True
	ingestReults['ingestUUID'] = ingestUUID
	ingestReults['accessPath'] = accessPath

	print(ingestReults)
	return ingestReults

if __name__ == '__main__':
	main()
