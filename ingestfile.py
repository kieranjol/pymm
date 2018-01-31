#!/usr/bin/env python3
#
# pymm is a python port of mediamicroservices
# (https://github.com/mediamicroservices/mm)
#
# `ingestfile` takes an input a/v file, transcodes a derivative,
# produces/extracts some metadata, creates fixity checks,
# and packages the whole lot in an OAIS-like Archival Information Package
#
# @fixme = stuff to do

import sys
import subprocess
import os
import argparse
# nonstandard libraries:
# from ffmpy import FFprobe, FFmpeg
# local modules:
import pymmFunctions
from pymmFunctions import *
import makeDerivs
import moveNcopy
import makeMetadata

pymmConfig = pymmFunctions.read_config()
pymmFunctions.check_missing_ingest_paths(pymmConfig)

yes = ('YES','Yes','yes','y','Y')
no = ('NO','No','no','n','N')

########################################################
#
#  INITIALIZE COMMAND LINE ARGUMENTS
#
parser = argparse.ArgumentParser()
parser.add_argument('-x','--interactiveMode',help='enter interactive mode for command line usage',action='store_true')
parser.add_argument('-i','--inputFilepath',help='path of input file')
parser.add_argument('-m','--mediaID',help='mediaID for input file')
parser.add_argument('-u','--operator',help='name of the person doing the ingest')
parser.add_argument('-t','--ingest_type',choices=['film scan','video transfer','multi-pak'],default='video transfer',help='type of file being ingested: film scan, video xfer, multi-pak for a collection of files')
parser.add_argument('-o','--output_path',help='output path for ingestfile')
parser.add_argument('-a','--aip_path',help='destination for Archival Information Package')
parser.add_argument('-r','--resourcespace_deliver',help='path for resourcespace proxy delivery')
parser.add_argument('-d','--database_reporting',help='report preservation metadata/events to database',action='store_true')

args = parser.parse_args()
# print(args)
interactiveMode = args.interactiveMode
inputFilepath = args.inputFilepath
mediaID = args.mediaID
operator = args.operator
output_path = args.output_path
aip_path = args.aip_path
resourcespace_deliver = args.resourcespace_deliver
report_to_db = args.database_reporting
ingest_type = args.ingest_type
cleanupStrategy = True

#
# END INTIALIZE COMMAND LINE ARGUMENTS
#
########################################################

requiredPaths = ['inputFilepath','mediaID','operator']

if interactiveMode == False:
	# Quit if there are required variables missing
	missingPaths = 0
	for flag in requiredPaths:
		if getattr(args,flag) == None:
			print('''
				CONFIGURATION PROBLEM:
				YOU FORGOT TO SET '''+flag+'''. It is required.
				Try again, but set '''+flag+''' with the flag --'''+flag
				)
			missingPaths += 1
	if missingPaths > 0:
		sys.exit()
else:
	# ask operator/mediaID/input file
	operator = input("Please enter your name: ")
	inputFilepath = input("Please drag the file you want to ingest into this window___").rstrip()
	mediaID = input("Please enter a valid mediaID for the input file (only use 'A-Z' 'a-z' '0-9' '_' or '-') : ")

if inputFilepath:
	filename = os.path.basename(inputFilepath)

# INIT A DICTIONARY FOR INGEST TYPES AND DERIV TYPES
ingests = {'video transfer':'proresHQ'}


# SET UP AIP DIRECTORY PATHS FOR INGEST...
# @fixme REDO THESE WITH OS.PATH.JOIN
packageOutputDir = pymmConfig['paths']['outdir_ingestfile']+'/'+mediaID+'/'
packageObjectDir = packageOutputDir+'objects/'
# packageDerivDir = os.path.join(packageObjectDir,'access')
packageMetadataDir = packageOutputDir+'metadata/'
packageFileMetadataDir = packageMetadataDir+'fileMeta/'
packageMetadataObjects = packageFileMetadataDir+'objects/'
packageLogDir = packageMetadataDir+'logs/'
packageDirs = [packageOutputDir,packageObjectDir,packageMetadataDir,packageFileMetadataDir,packageMetadataObjects,packageLogDir]

# ... THEN SEE IF THE TOP DIR EXISTS ...
if os.path.isdir(packageOutputDir):
	print('''
		It looks like '''+mediaID+''' was already ingested.
		If you want to replace the existing package please delete the package at
		'''+packageOutputDir+'''
		and then try again.
		''')
	sys.exit()

# ... AND OTHERWISE MAKE THEM ALL
for directory in packageDirs:
	os.mkdir(directory)

# set up a logfile for this ingest instance
ingestLogPath = packageLogDir+mediaID+'_'+pymmFunctions.timestamp('now')+'_ingestfile-log.txt'
with open(ingestLogPath,'x') as ingestLog:
	print('Laying a log at '+ingestLogPath)
ingestLogBoilerplate = [ingestLogPath,mediaID,filename,operator]
ingest_log(ingestLogPath,mediaID,filename,operator,'start','start')	

# INSERT DATABASE RECORD FOR THIS INGEST (log 'ingestion start')

# check if the input is recognized as an AV file @fixme: redo this with rediddled is_av() function
if not is_video(inputFilepath):
	is_av == False
	status = 'warning'
	message = "WARNING: "+filename+" is not recognized as a video file."
	print(message)
	ingest_log(ingestLogPath,mediaID,filename,operator,message,status)
	if not is_audio(inputFilepath):
		status = 'warning'
		message = "WARNING: "+filename+" is not recognized as an audio file."
		print(message)
		ingest_log(ingestLogPath,mediaID,filename,operator,message,status)

		if interactiveMode:
			stayOrGo = input("If you want to quit press 'q' and hit enter, otherwise press any other key:")
			if stayOrGo == 'q':
				sys.exit()
				# CLEANUP AND LOG THIS @fixme
			else:
				if is_av == False:
					ingest_log(ingestLogPath,mediaID,filename)
				pass
		else:
			print("Check your file and come back later. Now exiting. Bye!")
			sys.exit()

if interactiveMode:
	# cleanup strategy
	cleanupStrategy = input("Do you want to clean up stuff when you are done? yes/no : ")
	if cleanupStrategy in yes:
		cleanupStrategy = True
	elif cleanupStrategy in no:
		cleanupStrategy = False
	else:
		cleanupStrategy = False
		print("Sorry, your answer didn't make sense so we will just leave things where they are when we finish.")


# LOG THAT WE ARE STARTING
pymm_log(filename,mediaID,operator,'','STARTING')

# WRITE VARIABLES TO INGEST LOG

# CHECK INPUT FILE AGAINST MEDIACONCH POLICIES
if ingest_type == 'film scan':
	policyStatus = pymmFunctions.check_policy(ingest_type,inputFilepath)
	if policyStatus:
		message = filename+" passed the MediaConch policy check."
		status = "ok"
	else:
		message = filename+" did not pass the MediaConch policy check."
		status = "not ok, but not critical?"

	ingest_log(ingestLogPath,mediaID,filename,operator,message,status)

# RSYNC THE INPUT FILE TO THE OUTPUT DIR
# BUT FIRST GET A HASH OF THE ORIGINAL FILE (can i do this in php for our upload process?)
sys.argv = ['','-i'+inputFilepath,'-d'+packageObjectDir,'-L'+packageLogDir]
moveNcopy.main()

# MAKE DERIVS
# WE'LL ALWAYS OUTPUT A RESOURCESPACE VERSION, SO INIT THE 
# DERIVTYPE LIST WITH RESOURCESPACE
derivTypes = ['resourcespace']
if ingest_type == 'film scan':
	derivTypes.append('filmMezzanine')
	for derivType in derivTypes:
		sys.argv = ['','-i'+inputFilepath,'-o'+packageObjectDir,'-d'+derivType,'-r'+packageLogDir]
		makeDerivs.main()
elif ingest_type == 'video transfer':
	derivTypes.append('proresHQ')
	for derivType in derivTypes:
		sys.argv = ['','-i'+inputFilepath,'-o'+packageObjectDir,'-d'+derivType,'-r'+packageLogDir]
		makeDerivs.main()
else:
	for derivType in derivTypes:
		sys.argv = ['','-i'+inputFilepath,'-o'+packageObjectDir,'-d'+derivType,'-r'+packageLogDir]
		makeDerivs.main()

# CHECK DERIVS AGAINST MEDIACONCH POLICIES

# MAKE METADATA
ingest_log(ingestLogPath,mediaID,filename,operator,"The input file MD5 hash is: "+makeMetadata.hash_file(inputFilepath),'OK')
mediainfo = makeMetadata.get_mediainfo_report(inputFilepath,packageMetadataDir)
if mediainfo:
	ingest_log(ingestLogPath,mediaID,filename,operator,"mediainfo XML report written to metadata directory for package.",'OK')
frameMD5 = makeMetadata.make_frame_md5(inputFilepath,packageMetadataDir)
if frameMD5 != False:
	ingest_log(ingestLogPath,mediaID,filename,operator,"frameMD5 report for input file written to metadata directory for package","OK")

# FINISH LOGGING

# RSYNC TO AIP STAGE

# VERIFY PACKAGE CHECKSUM
packageVerified = False

# CLEANUP
if cleanupStrategy == True and packageVerified == True:
	print("LET'S CLEEEEEAN!")
	cleanup_package(inputFilepath,packageOutputDir,reason)
else:
	print("BUH-BYE")