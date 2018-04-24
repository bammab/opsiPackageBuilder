# -*- coding: utf-8 -*-
"""
This module is part of the opsi PackageBuilder
see: https://forum.opsi.org/viewforum.php?f=22

Permission is hereby granted, free of charge, to any person
obtaining a copy of this software and associated documentation
files (the "Software"), to deal in the Software without
restriction, including without limitation the rights to use, 
copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software 
is furnished to do so, subject to the following conditions:

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, 
EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES 
OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. 
IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY 
CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, 
TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE 
SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
"""

__author__ = 'Holger Pandel'
__copyright__ = "Copyright 2013-2015, Holger Pandel"
__license__ = "MIT"
__maintainer__ = "Holger Pandel"
__email__ = "holger.pandel@googlemail.com"
__status__ = "Production"

import re
import sys
import json
import os
import socket
import spur
import shutil
import tempfile
import platform
from io import StringIO
import datetime
from pathlib import PurePath, PurePosixPath, Path

from PyQt5 import QtCore
from PyQt5.QtCore import QObject, pyqtSignal

import oPB
from oPB.core.confighandler import ConfigHandler
from oPB.core.tools import Helper, LogMixin

translate = QtCore.QCoreApplication.translate


class OpsiProcessing(QObject, LogMixin):
    """Opsi server job online processing class"""

    progressChanged = pyqtSignal(str)
    """Signal, emitted on change in job processing"""

    # def __init__(self, project, project_folder, server="127.0.0.1", port=22, username = None, password = None, keyfile = None, cfg = None):
    def __init__(self, control = None, hostkey_policy = spur.ssh.MissingHostKey.warn):
        """
        Constructor of OpsiProcessing

        Initialises job processing.

        :param control: backend controlData object
        """
        super().__init__()
        self._control = control
        self._hostkey_policy = hostkey_policy

        self._shell = None
        self._server = None
        self._ip = None
        self._hide_secret_in_output = False
        self._sshuser = None
        self._sshpass = None
        self._sshkey = None

        self._hook = None
        self._server = ConfigHandler.cfg.opsi_server

        self._sshuser = ConfigHandler.cfg.opsi_user
        self._sshpass = ConfigHandler.cfg.opsi_pass
        self._sshkey = ConfigHandler.cfg.keyfilename
        self._sshport = ConfigHandler.cfg.sshport

        self.ret = oPB.RET_OK
        self.rettype = oPB.MsgEnum.MS_INFO
        self.retmsg = translate("OpsiProcessing", "Command executed.")

    def run(self, action, **kwargs):
        """
        Starts job processing.

        :param action: job type to run (:data:`.OpEnum` for job types)
        :param kwargs: optional arguments depending on job type, see single job section below
        :return: return (return code oPB.RET_*, msg type :data:`.MsgEnum`), msg text)
        :rtype: tuple

        Following additional ``**kwargs`` options are recognized, depending on job action:

            - Always recognized: alt_destination, alt_user, alt_pass - overrides opsi server parameters from INI file
            - DO_INSTALL: depot
            - DO_INSTSETUP: depot
            - DO_UNINSTALL: depot
            - DO_QUICKINST: depot, packagefile
            - DO_UPLOAD: depot, packagefile
            - DO_QUICKUNINST: depot, productlist
            - DO_CREATEJOBS: clients, products, ataction, dateVal, timeVal, on_demand, wol
            - DO_DELETEJOBS: joblist
            - DO_DELETEFILEFROMREPO: packages
            - DO_UNREGISTERDEPOT: depot
            - DO_DEPLOY: clientlist, options
            - DO_GENMD5: packages
            - DO_SETRIGHTS: package
            - DO_GETLOCKEDPRODUCTS: depot
            - DO_UNLOCKPRODUCTS: depot, productlist

        """

        #options = {
        #    'prodver' : '1.0',
        #    'packver' : '1',}
        #options.update(kwargs)
        #packname = self.control.name + "_" + options['prodver'] + "-" + options['packver'] + ".opsi"

        self._hide_secret_in_output = []

        # override server name/ user or pass to use
        alt_destination = kwargs.get("alt_destination", "")
        if alt_destination != "":
            self._server = alt_destination

        # individual user / pass for shell
        # if not set, ConfigHandler.cfg.opsi_user + ConfigHandler.cfg.opsi_pass are used
        alt_user = kwargs.get("alt_user", "")
        if alt_user != "":
            self._sshuser = alt_user

        alt_pass = kwargs.get("alt_pass", "")
        if alt_pass != "":
            self._sshpass = alt_pass

        # set following environment variables on every ssh command
        self._env = {"PYTHONIOENCODING" : "'utf-8'", "POSIXLY_CORRECT" : "1"}

        self.logger.debug("Executing action: " + str(oPB.OpEnum(action)))

        #if not self._control is None:
        #    self.logger.sshinfo("Local path: " + self._control.local_package_path)
        #    self.logger.sshinfo("Path on server: " + self._control.path_on_server + "/" + self._control.packagename)

        # temporary file path for copy operations
        tmppath = oPB.UNIX_TMP_PATH

        # change dev_base if necessary
        if ConfigHandler.cfg.wb_new == "True":
            oPB.DEV_BASE = oPB.DEV_BASE_OPSI41
        else:
            oPB.DEV_BASE = oPB.DEV_BASE_OPSI40

        # change opsi commands based on version setting
        if ConfigHandler.cfg.is_opsi41 == "True":
            oPB.OPB_PROD_UPDATER = oPB.OPB_PROD_UPDATER_41
            oPB.OPB_GETPRODUPD_PID = oPB.OPB_GETPRODUPD41_PID
        else:
            oPB.OPB_PROD_UPDATER = oPB.OPB_PROD_UPDATER_40
            oPB.OPB_GETPRODUPD_PID = oPB.OPB_GETPRODUPD40_PID

        # define empty result as default
        result = []

        # ------------------------------------------------------------------------------------------------------------------------
        if action == oPB.OpEnum.DO_BUILD:
            ret = oPB.RET_PEXISTS
            self.logger.ssh(20 * "-" + "ACTION: BUILD" + 20 * "-")
            if (self._control.packagename in os.listdir(self._control.projectfolder)):
                self.logger.ssh("Package already build.")
                self.logger.warning("Set return code to RET_PEXISTS")
                self.ret = ret
                self.rettype = oPB.MsgEnum.MS_WARN
                self.retmsg = translate("OpsiProcessing", "Package has been build before. It will not be overwritten!")
            else:
                # cmd = ConfigHandler.cfg.buildcommand + " & sync & sleep 1"
                cmd = ["sh", "-c", ConfigHandler.cfg.buildcommand]

                result = self._processAction(cmd, action, ret, split = False)

        # ------------------------------------------------------------------------------------------------------------------------
        if action == oPB.OpEnum.DO_INSTALL:
            ret = oPB.RET_PINSTALL
            depot = kwargs.get("depot", self._server)

            self.logger.ssh(20 * "-" + "ACTION: INSTALL" + 20 * "-")

            if not (self._control.packagename in os.listdir(self._control.projectfolder)):
                self.logger.ssh("Package not available: " + self._control.local_package_path)
                self.logger.warning("Set return code to RET_PINSTALL")
                self.ret = ret
                self.rettype = oPB.MsgEnum.MS_ERR
                self.retmsg = translate("OpsiProcessing", "Package file could not be found!")
            else:
                if ConfigHandler.cfg.use_depot_funcs == "False":
                    cmd = ConfigHandler.cfg.installcommand + " " + self._control.packagename
                else:
                    cmd = oPB.OPB_INSTALL + " " + oPB.OPB_DEPOT_SWITCH + " " + depot + " " + self._control.packagename

                result = self._processAction(cmd, action, ret)

        # ------------------------------------------------------------------------------------------------------------------------
        if action == oPB.OpEnum.DO_INSTSETUP:
            ret = oPB.RET_PINSTSETUP
            depot = kwargs.get("depot", self._server)

            self.logger.ssh(20 * "-" + "ACTION: INSTALLSETUP" + 20 * "-")
            if not (self._control.packagename in os.listdir(self._control.projectfolder)):
                self.logger.ssh("Package not available.")
                self.logger.warning("Set return code to RET_PINSTSETUP")
                self.ret = ret
                self.rettype = oPB.MsgEnum.MS_ERR
                self.retmsg = translate("OpsiProcessing", "Package file could not be found!")
            else:
                if ConfigHandler.cfg.use_depot_funcs == "False":
                    cmd = ConfigHandler.cfg.instsetupcommand + " " + self._control.packagename
                else:
                    cmd = oPB.OPB_INSTSETUP + " " + oPB.OPB_DEPOT_SWITCH + " " + depot + " " + self._control.packagename

                result = self._processAction(cmd, action, ret)

        # ------------------------------------------------------------------------------------------------------------------------
        if action == oPB.OpEnum.DO_UNINSTALL:
            ret = oPB.RET_PUNINSTALL
            depot = kwargs.get("depot", self._server)

            self.logger.ssh(20 * "-" + "ACTION: UNINSTALL" + 20 * "-")
            if ConfigHandler.cfg.use_depot_funcs == "False":
                cmd = ConfigHandler.cfg.uninstallcommand + " " + self._control.id
            else:
                cmd = oPB.OPB_UNINSTALL + " " + oPB.OPB_DEPOT_SWITCH + " " + depot + " " + self._control.id

            result = self._processAction(cmd, action, ret)

        # ------------------------------------------------------------------------------------------------------------------------
        if action == oPB.OpEnum.DO_SETRIGHTS:
            ret = oPB.RET_SSHCMDERR
            package = kwargs.get("package", self._control.path_on_server)
            self.logger.ssh(20 * "-" + "ACTION: SET RIGHTS" + 20 * "-")
            if ConfigHandler.cfg.age == "True":
                cmd = oPB.OPB_SETRIGHTS_SUDO + " '" + package + "'"
                if ConfigHandler.cfg.sudo == "True":
                    cmd = "echo '" + ConfigHandler.cfg.opsi_pass + "' | sudo -S " + cmd
                else:
                    cmd = "sudo " + cmd
            else:
                self._sshuser = "root"
                self._sshpass = ConfigHandler.cfg.root_pass
                cmd = oPB.OPB_SETRIGHTS_NOSUDO + " '" + package + "'"

            cmd = ["sh", "-c", cmd]
            result = self._processAction(cmd, action, ret, split = False, cwd = False)

        # ------------------------------------------------------------------------------------------------------------------------
        if action == oPB.OpEnum.DO_QUICKINST:
            ret = oPB.RET_PINSTALL
            depot = kwargs.get("depot", self._server)
            package = kwargs.get("packagefile", "")

            # check if temporary install folder exists and create if not
            destfile = str(PurePosixPath(tmppath, PurePath(package).name))

            self.logger.ssh(20 * "-" + "ACTION: QUICKINSTALL" + 20 * "-")
            # copy file to temporary location
            try:
                self.logger.ssh("Copy file: " + package + " --> " + destfile)
                self.copyToRemote(package, destfile)

                if ConfigHandler.cfg.use_depot_funcs == "False":
                    cmd = ConfigHandler.cfg.installcommand + " " + destfile
                else:
                    cmd = oPB.OPB_INSTALL + " " + oPB.OPB_DEPOT_SWITCH + " " + depot + " " + destfile

                result = self._processAction(cmd, action, ret)

            except Exception as error:
                self.logger.ssh("Could not copy file to remote destination.")
                self.logger.error(repr(error).replace("\\n"," --> "))
                self.logger.error("Set return code to RET_SSHCONNERR")
                self.ret = oPB.RET_SSHCONNERR
                self.rettype = oPB.MsgEnum.MS_ERR
                self.retmsg = translate("OpsiProcessing", "Error establishing SSH connection. See Log for details.")

        # ------------------------------------------------------------------------------------------------------------------------
        if action == oPB.OpEnum.DO_UPLOAD:
            ret = oPB.RET_PUPLOAD
            package = kwargs.get("packagefile", "")
            depot = kwargs.get("depot", self._server)

            # check if temporary install folder exists and create if not
            destfile = str(PurePosixPath(tmppath, PurePath(package).name))

            self.logger.ssh(20 * "-" + "ACTION: UPLOAD" + 20 * "-")
            # copy file to temporary location
            try:
                self.logger.ssh("Copy file: " + package + " --> " + destfile)
                self.copyToRemote(package, destfile)

                if ConfigHandler.cfg.use_depot_funcs == "False":
                    cmd = ConfigHandler.cfg.uploadcommand + " " + destfile
                else:
                    cmd = oPB.OPB_UPLOAD + " " + oPB.OPB_DEPOT_SWITCH + " " + depot + " " + destfile

                result = self._processAction(cmd, action, ret)

            except Exception as error:
                self.logger.ssh("Could not copy file to remote destination.")
                self.logger.error(repr(error).replace("\\n"," --> "))
                self.logger.error("Set return code to RET_SSHCONNERR")
                self.ret = oPB.RET_SSHCONNERR
                self.rettype = oPB.MsgEnum.MS_ERR
                self.retmsg = translate("OpsiProcessing", "Error establishing SSH connection. See Log for details.")

        # ------------------------------------------------------------------------------------------------------------------------
        if action == oPB.OpEnum.DO_IMPORT:
            ret = oPB.RET_SSHCMDERR
            package = kwargs.get("packagefile", "")

            # get project name
            file = Helper.get_file_from_path(package)
            product = file[:file.rfind("_")]  # remove everything behind product name (version, file extension, etc.)

            # check if temporary install folder exists and create if not
            if platform.system() == "Windows":
                destfolder = Helper.concat_path_posix(oPB.DEV_BASE, ConfigHandler.cfg.dev_dir[3:]).replace("\\","/")
                destfile = Helper.concat_path_posix(destfolder, file)
            else:
                destfolder = ConfigHandler.cfg.dev_dir
                destfile = Helper.concat_path_native(ConfigHandler.cfg.dev_dir, file)

            self.logger.debug("Dest. folder: " + destfolder)
            self.logger.debug("Dest. file: " + destfile)

            self.logger.ssh(20 * "-" + "ACTION: IMPORT" + 20 * "-")
            # copy file to temporary location
            try:
                self.progressChanged.emit(translate("OpsiProcessing", "Copying file to:") + " " + destfile)
                self.logger.ssh("Copy file: " + package + " --> " + destfile)
                self.copyToRemote(package, destfile)

                self.progressChanged.emit(translate("OpsiProcessing", "Extracting..."))
                cmd = ["sh", "-c", "cd " + destfolder + "; " + oPB.OPB_EXTRACT + " " + destfile]
                result = self._processAction(cmd, action, ret, split = False, cwd = False)
            except Exception as error:
                self.logger.ssh("Could not copy file to remote destination.")
                self.logger.error(repr(error).replace("\\n"," --> "))
                self.logger.error("Set return code to RET_SSHCONNERR")
                self.ret = oPB.RET_SSHCONNERR
                self.rettype = oPB.MsgEnum.MS_ERR
                self.retmsg = translate("OpsiProcessing", "Error establishing SSH connection. See Log for details.")
            else:
                self.logger.ssh("Setting right after decompression: " + Helper.concat_path_posix(destfolder, product))
                self.run(oPB.OpEnum.DO_SETRIGHTS, package=Helper.concat_path_posix(destfolder, product))

        # ------------------------------------------------------------------------------------------------------------------------
        if action == oPB.OpEnum.DO_GETCLIENTS:
            ret = oPB.RET_SSHCMDERR
            self.logger.ssh(20 * "-" + "ACTION: GET CLIENTS" + 20 * "-")

            result = self._processAction(oPB.OPB_METHOD_GETCLIENTS, action, ret, cwd = False)

            result = json.loads(result)

        # ------------------------------------------------------------------------------------------------------------------------
        if action == oPB.OpEnum.DO_GETCLIENTGROUPS:
            ret = oPB.RET_SSHCMDERR
            self.logger.ssh(20 * "-" + "ACTION: GET CLIENTGROUPS" + 20 * "-")

            result = self._processAction(oPB.OPB_METHOD_GETCLIENTGROUPS, action, ret, cwd = False)

            result = json.loads(result)

        # ------------------------------------------------------------------------------------------------------------------------
        if action == oPB.OpEnum.DO_GETGROUPS:
            ret = oPB.RET_SSHCMDERR
            self.logger.ssh(20 * "-" + "ACTION: GET GROUPS" + 20 * "-")

            result = self._processAction(oPB.OPB_METHOD_GETGROUPS, action, ret, cwd = False)

            result = json.loads(result)

        # ------------------------------------------------------------------------------------------------------------------------
        if action == oPB.OpEnum.DO_GETPRODUCTS:
            ret = oPB.RET_SSHCMDERR
            self.logger.ssh(20 * "-" + "ACTION: GET PRODUCTS" + 20 * "-")

            result = self._processAction(oPB.OPB_METHOD_GETPRODUCTS, action, ret, cwd = False)

            result = json.loads(result)

        # ------------------------------------------------------------------------------------------------------------------------
        if action == oPB.OpEnum.DO_GETCLIENTSONDEPOTS:
            ret = oPB.RET_SSHCMDERR
            self.logger.ssh(20 * "-" + "ACTION: GET CLIENTS ON DEPOTS" + 20 * "-")

            result = self._processAction(oPB.OPB_METHOD_GETCLIENTSONDEPOTS, action, ret, cwd = False)

            result = json.loads(result)

            # filter example:
            # for row in result:
            #     print(row["clientId"]) if row["depotId"] == "yi7xa19z.host.domain.de" else None
        # ------------------------------------------------------------------------------------------------------------------------
        if action == oPB.OpEnum.DO_QUICKUNINST:
            ret = oPB.RET_PUNINSTALL
            productlist = kwargs.get("productlist", [])
            depot = kwargs.get("depot", self._server)

            self.logger.ssh(20 * "-" + "ACTION: QUICK UNINSTALL" + 20 * "-")
            for p in productlist:
                self.logger.sshinfo("Current selection: " + p)
                if ConfigHandler.cfg.use_depot_funcs == "False":
                    cmd = ConfigHandler.cfg.uninstallcommand + " " + p
                else:
                    cmd = oPB.OPB_UNINSTALL + " " + oPB.OPB_DEPOT_SWITCH + " " + depot + " " + p

                result = self._processAction(cmd, action, ret)

        # ------------------------------------------------------------------------------------------------------------------------
        if action == oPB.OpEnum.DO_GETLOCKEDPRODUCTS:
            ret = oPB.RET_SSHCMDERR
            depot = kwargs.get("depot", self._server)
            self.logger.ssh(20 * "-" + "ACTION: GET LOCKED PRODUCTS" + 20 * "-")
            # OPB_METHOD_GETLOCKEDPRODUCTS + '{"depotId":"host.domain.de", "locked":true}'

            try:
                cmd = oPB.OPB_METHOD_GETLOCKEDPRODUCTS + " '{" + '"locked": true, "depotId":"' + depot + '"}' + "'"
                result = self._processAction(cmd, action, ret, cwd=False)

                result = json.loads(result)
            except:
                result = []

        # ------------------------------------------------------------------------------------------------------------------------
        if action == oPB.OpEnum.DO_UNLOCKPRODUCTS:
            ret = oPB.RET_PUNLOCK
            productlist = kwargs.get("productlist", [])
            depot = kwargs.get("depot", self._server)

            self.logger.ssh(20 * "-" + "ACTION: UNLOCK PRODUCT(S)" + 20 * "-")
            for p in productlist:
                self.logger.sshinfo("Current selection: " + p)
                # opsi-admin -d method productOnDepot_getObjects '[]' '{"productId":"testdummy", "depotId":"host.domain.de"}' | sed  -e 's/"locked"\s:\strue/"locked" : false/' > /tmp/update_objects.json
                cmd = oPB.OPB_METHOD_GETLOCKEDPRODUCTS + " '{" + '"productId":' + '"' + p + '"' + ', "depotId":"' + depot + '"}' + "'" + oPB.OPB_METHOD_UNLOCKPRODUCTS
                # first, create json file with updated information
                try:
                    result = self._processAction(cmd, action, ret, cwd=False)
                except:
                    result = []
                    break
                # second initiate update of object properties
                try:
                    cmd = ["sh", "-c", oPB.OPB_METHOD_UPDATEOBJECTS]
                    result = self._processAction(cmd, action, ret, split = False, cwd = False)
                except:
                    result = []
                    break

        # ------------------------------------------------------------------------------------------------------------------------
        if action == oPB.OpEnum.DO_GETATJOBS:
            ret = oPB.RET_SSHCMDERR
            self.logger.ssh(20 * "-" + "ACTION: GET AT JOBS" + 20 * "-")

            cmd_queue = oPB.OPB_AT_QUEUE # get complete AT queue only
            cmd_detail_all = ["sh", "-c", oPB.OPB_AT_JOB_DETAIL] # get the details for every AT job in queue

            jobids = []
            try:
                # fetch job ids
                result = self._processAction(cmd_queue, action, ret, cwd = False)
                # returns lines like this: ['9975', '22 May 2015', '20:11:00', 'opsiadm']
                jobids = ([[str(e[3]) + " " + e[2] + " " + str(e[5]), e[4], int(e[0]), e[7]] for e in (line.split() for line in result.splitlines())])

                if jobids:
                    # the following block result in a jobaction list like so:
                    # ['yi7xa102.host.domain.de', '', 'on_demand']
                    # ['yi7xa101.host.domain.de', '7zip', 'setup']
                    # ....
                    jobactions = []
                    result = self._processAction(cmd_detail_all, action, ret, split = False, cwd = False)
                    result = result.splitlines()

                    for i, row in enumerate(result):
                        if oPB.OPB_METHOD_ONDEMAND in result[i]:
                            result[i] = row.replace(oPB.OPB_METHOD_ONDEMAND, 'on_demand')
                        if oPB.OPB_METHOD_PRODUCTACTION in result[i]:
                            result[i] = row.replace(oPB.OPB_METHOD_PRODUCTACTION, '')
                        if oPB.OPB_METHOD_WOL in result[i]:
                            result[i] = row.replace(oPB.OPB_METHOD_WOL, 'wol')

                    for row in result:
                        row = row.split()
                        if len(row) == 2:   # on demand / wol
                            jobactions.append([row[1], "", row[0]])
                        elif len(row) == 3:   # product action
                            jobactions.append([row[1], row[0], row[2]])
                        else:
                            jobactions.append("N/A", "N/A", (" ").join(row))

                    result = []
                    for i, id in enumerate(jobids):
                        result.append(jobactions[i] + jobids[i])

            except:
                result = []

        # ------------------------------------------------------------------------------------------------------------------------
        if action == oPB.OpEnum.DO_CREATEJOBS:
            ret = oPB.RET_SSHCMDERR
            #clients = clIdx, products = prodIdx, ataction = action, dateVal = date, timeVal = time, on_demand = od, wol = wol
            clients = kwargs.get("clients", [])
            products = kwargs.get("products", [])
            ataction = kwargs.get("ataction", "setup")
            dateVal = kwargs.get("dateVal", "29991230")
            timeVal = kwargs.get("timeVal", "2359")
            on_demand = kwargs.get("on_demand", False)
            wol = kwargs.get("wol", False)

            self.logger.ssh(20 * "-" + "ACTION: CREATE AT JOBS" + 20 * "-")
            cmd = oPB.OPB_AT_CREATE # create a new AT job

            destfile = str(PurePosixPath(tmppath, "atjob.lst"))

            # File content:
            # echo "opsi-admin -d method powerOnHost yi7xa100.host.domain.de" | at -q D -t 201505231710
            # echo opsi-admin -d method setProductActionRequestWithDependencies 7zip yi7xa100.host.domain.de setup | at -q D -t 201505231725
            # echo "opsi-admin -d method hostControl_fireEvent 'on_demand' yi7xa100.host.domain.de" | at -q D -t 201505231725

            if wol is True:
                try:
                    wcfg = int(ConfigHandler.cfg.wol_lead_time)
                except:
                    wcfg = 0

                woltime_full = datetime.datetime(100,1,1,int(timeVal[:2]),int(timeVal[2:])) - datetime.timedelta(minutes=wcfg)
                woltime = (str(woltime_full.hour) if len(str(woltime_full.hour)) == 2 else '0' + str(woltime_full.hour)) + \
                          (str(woltime_full.minute) if len(str(woltime_full.minute)) == 2 else '0' + str(woltime_full.minute))

            f = None
            if clients:
                with tempfile.TemporaryFile(mode = "w+", encoding='UTF-8') as f:
                    for client in clients:
                        for product in products:
                            if wol is True:
                                f.write('echo "' + oPB.OPB_METHOD_WOL + " " + client + '" | ' + oPB.OPB_AT_CREATE + " " + dateVal + woltime + "\n")

                            f.write('echo "' + oPB.OPB_METHOD_PRODUCTACTION + " " + product + " " + client + " " + ataction + '" | ' + oPB.OPB_AT_CREATE +
                                    " " + dateVal + timeVal + "\n")

                            if on_demand is True:
                                f.write('echo "' + oPB.OPB_METHOD_ONDEMAND + " " + client + '" | ' + oPB.OPB_AT_CREATE + " " + dateVal + timeVal + "\n")

                    f.flush()
                    f.seek(0)
                    self.copyToRemote(f, destfile, True)

                cmd = ["sh", destfile]
                result = self._processAction(cmd, action, ret, split = False, cwd = False)

        # ------------------------------------------------------------------------------------------------------------------------
        if action == oPB.OpEnum.DO_DELETEJOBS:
            ret = oPB.RET_SSHCMDERR
            joblist = kwargs.get("joblist", [])
            self.logger.ssh(20 * "-" + "ACTION: DELETE AT JOBS" + 20 * "-")

            destfile = str(PurePosixPath(tmppath, "atjob.lst"))

            f = None
            if joblist:
                with tempfile.TemporaryFile(mode = "w+", encoding='UTF-8') as f:
                    for elem in joblist:
                        f.write(oPB.OPB_AT_REMOVE + " " + elem + "\n")
                    f.flush()
                    f.seek(0)
                    self.copyToRemote(f, destfile, True)

                cmd = ["sh", destfile]
                result = self._processAction(cmd, action, ret, split = False, cwd = False)

        # ------------------------------------------------------------------------------------------------------------------------
        if action == oPB.OpEnum.DO_DELETEALLJOBS:
            ret = oPB.RET_SSHCMDERR
            self.logger.ssh(20 * "-" + "ACTION: DELETE ALL AT JOBS" + 20 * "-")
            cmd = ["sh", "-c", oPB.OPB_AT_REMOVE_ALL] # delete ANY AT Job in queue D

            result = self._processAction(cmd, action, ret, split = False, cwd = False)

        # ------------------------------------------------------------------------------------------------------------------------
        if action == oPB.OpEnum.DO_GETREPOCONTENT:
            ret = oPB.RET_NOREPO
            self.logger.ssh(20 * "-" + "ACTION: GET REPOSITORY CONTENT" + 20 * "-")

            cmd = ["sh", "-c", oPB.OPB_GETREPOCONTENT]

            # returns list
            # line like: 6eaedc322b4de839338f8855351c6024-@MD5@-7zip_9.22-1.opsi
            result = self._processAction(cmd, action, ret, split = False, cwd = False)

            tmplist = []

            try:
                # 0)Split into MD5 and file name
                # 1) Product name
                # create product string from answer, i.e.: win2003_server_4.0.3-1.opsi
                # first separate product name: win2003_server
                # it's complex, we have to find first occurence of "_" in front
                # of a number (=major product version begin)
                for line in result.splitlines():
                    errstring = "Error "
                    file = line.split("-@MD5@-")[1]
                    md5 = line.split("-@MD5@-")[0]

                    product = file[:file.rfind("_")]
                    version = (file[file.rfind("_")+1:file.rfind(".")]).split("-")

                    if md5.strip() == "":
                        errstring += " / MD5 file unaccessible"
                        self.logger.error("Error while accessing MD5 file for file: " + file)
                        self.logger.error("You have to generate MD5 file or set rights on repository folder." + file)
                        warn_md5 = True
                    else:
                        warn_md5 = False

                    if len(version) != 2:
                        errstring += "incorrect version data"
                        self.logger.error("Error in package filename: " + file)
                        self.logger.error("Version numbering scheme is wrong." + file)
                        warn_ver = True
                    else:
                        warn_ver = False

                    try:
                        prod_ver = version[0]
                        pack_ver = version[1]
                    except:
                        prod_ver = "(unavail)"
                        pack_ver = "(unavail)"

                    if warn_ver or warn_md5:
                        tmplist.append(product + ";" + errstring + ";" + prod_ver + ";" + pack_ver)
                    else:
                        tmplist.append(product + ";" + md5 + ";" + prod_ver + ";" + pack_ver)

                    if warn_ver or warn_md5:
                        self.ret = ret
                        self.rettype = oPB.MsgEnum.MS_INFO
                        self.retmsg = translate("OpsiProcessing", "Error during command execution. Check Log for details.")

                result = tmplist

            except:
                result = []
        # ------------------------------------------------------------------------------------------------------------------------
        if action == oPB.OpEnum.DO_GETDEPOTS:
            ret = oPB.RET_SSHCMDERR
            self.logger.ssh(20 * "-" + "ACTION: GET DEPOTS" + 20 * "-")

            result = self._processAction(oPB.OPB_METHOD_GETDEPOTS, action, ret, cwd = False)

            if not result == {}:
                result = json.loads(result)
            else:
                result = []

        # ------------------------------------------------------------------------------------------------------------------------
        if action == oPB.OpEnum.DO_GETPRODUCTSONDEPOTS:
            ret = oPB.RET_SSHCMDERR
            self.logger.ssh(20 * "-" + "ACTION: GET PRODUCTS ON DEPOTS" + 20 * "-")

            #result list: "productid;type;prod_ver;pack_ver;server"
            result = self._processAction(oPB.OPB_METHOD_GETPRODUCTSONDEPOTS, action, ret, cwd = False)

            if not result == {}:
                result = json.loads(result)
            else:
                result = []

        # ------------------------------------------------------------------------------------------------------------------------
        if action == oPB.OpEnum.DO_DELETEFILEFROMREPO:
            ret = oPB.RET_SSHCMDERR
            packages = kwargs.get("packages", [])
            self.logger.ssh(20 * "-" + "ACTION: DELETE FILE(S) FROM REPOSITORY" + 20 * "-")

            if packages:
                packstring = (" ").join(packages)
                cmd = ["sh", "-c", "PACKETS=\"" + packstring + "\"; " + oPB.OPB_DEPOT_FILE_REMOVE]
                result = self._processAction(cmd, action, ret, split = False, cwd = False)

        # ------------------------------------------------------------------------------------------------------------------------
        if action == oPB.OpEnum.DO_UNREGISTERDEPOT:
            ret = oPB.RET_SSHCMDERR
            depot = kwargs.get("depot", "")
            self.logger.ssh(20 * "-" + "ACTION: UNREGISTER DEPOT AT CONFIG SERVER" + 20 * "-")

            if depot:
                cmd = oPB.OPB_METHOD_UNREGISTERDEPOT + " " + depot
                result = self._processAction(cmd, action, ret, cwd = False)

        # ------------------------------------------------------------------------------------------------------------------------
        if action == oPB.OpEnum.DO_DEPLOY:
            ret = oPB.RET_NOWINEXE
            clientlist = kwargs.get("clientlist", [])
            options = kwargs.get("options", {})
            self.logger.ssh(20 * "-" + "ACTION: DEPLOY OPSI-CLIENT-AGENT" + 20 * "-")

            result = self._processAction(oPB.OPB_PRECHECK_WINEXE, action, ret, cwd = False)
            if result != {}:
                ret = oPB.RET_SSHCMDERR

                destfile = Helper.concat_path_posix(tmppath, "deploy.sh")

                cmd = ""
                cmd += oPB.OPB_DEPLOY_COMMAND
                cmd += " -u " + "'" + options["user"] + "'"
                cmd += " -p " + "'" + options["pass"] + "'"
                cmd += " -c " if options["usefqdn"] else ""
                cmd += " -x " if options["ignoreping"] else ""
                cmd += " -S " if options["skipexisting"] else ""
                #cmd += " -v " if not options["proceed"] else ""
                cmd += " -v "

                if options["post_action"] == "":
                    pass
                elif options["post_action"] == "startclient":
                    cmd += " -o "
                elif options["post_action"] == "reboot":
                    cmd += " -r "
                elif options["post_action"] == "shutdown":
                    cmd += " -s "

                if clientlist:
                    self.logger.ssh("Create mass deploy script under /tmp/deploy.sh")
                    f = None
                    with tempfile.TemporaryFile(mode = "w+", encoding='UTF-8') as f:

                        for client in clientlist:
                            # optionally, execute command on client before deploying the client
                            if options["pre_action"] != "":
                                f.write("\n")
                                tmp = "cat </dev/null | winexe --debug-stderr --user '" + options["user"] + "' --password '" + \
                                        options["pass"] + "' //" + client + " 'cmd /c " + options["pre_action"] + "'"
                                f.write(tmp + "\n")
                                f.write("sleep 5\n")
                            f.write(cmd + " " + client + "\n")
                        f.flush()
                        f.seek(0)
                        self.copyToRemote(f, destfile, True)

                    # chmod script executable
                    self.logger.ssh("Set executable bit")
                    cmd = ["sh", "-c", "chmod +x " + destfile]
                    result = self._processAction(cmd, action, ret, split = False, cwd = False)

                    # execute /tmp/deploy.sh script
                    if options["proceed"]:
                        cmd = ["sh", "-c", "nohup " + destfile + " >/tmp/opb-deploy.log & sleep 2 ; exit 0"]
                        result = self._processAction(cmd, action, ret, split=False, cwd = False)
                        if self.ret == oPB.RET_OK:
                            self.logger.sshinfo("Deploy initiated, see /tmp folder of opsi server for detailed deployment logs")
                    else:
                        cmd = ["sh", "-c", "chmod +x " + destfile + "; " + destfile]
                        result = self._processAction(cmd, action, ret, split = False, cwd = False)
                        if self.ret == oPB.RET_OK:
                            self.logger.sshinfo("opsi-client-agent successfully deployed.")

                    # chmod script executable
                    self.logger.ssh("Removing temporary deploy script")
                    cmd = ["sh", "-c", "rm -f " + destfile]
                    result = self._processAction(cmd, action, ret, split = False, cwd = False)

                # cd c:\TEMP\RZInstall\ETHLineSpeed & set NWDUPLEXMODE=AUTOSENS & start install.cmd

        # ------------------------------------------------------------------------------------------------------------------------
        if action == oPB.OpEnum.DO_SETRIGHTS_REPO:
            ret = oPB.RET_SSHCMDERR
            self.logger.ssh(20 * "-" + "ACTION: SET RIGHTS ON REPO" + 20 * "-")

            if ConfigHandler.cfg.age == "True":
                cmd = oPB.OPB_SETRIGHTS_SUDO + " '" + oPB.REPO_PATH + "'"
                if ConfigHandler.cfg.sudo == "True":
                    cmd = "echo '" + ConfigHandler.cfg.opsi_pass + "' | sudo -S " + cmd
                else:
                    cmd = "sudo " + cmd
            else:
                self._sshuser = "root"
                self._sshpass = ConfigHandler.cfg.root_pass
                cmd = oPB.OPB_SETRIGHTS_NOSUDO + " '" + oPB.REPO_PATH + "'"

            cmd = ["sh", "-c", cmd]
            result = self._processAction(cmd, action, ret, split = False, cwd = False)

        # ------------------------------------------------------------------------------------------------------------------------
        if action == oPB.OpEnum.DO_PRODUPDATER:
            ret = oPB.RET_PRODUPDRUN
            self.logger.ssh(20 * "-" + "ACTION: START OPSI PRODUCT UPDATER" + 20 * "-")

            cmd = ["sh", "-c", oPB.OPB_GETPRODUPD_PID]
            result = self._processAction(cmd, action, ret, split = False, cwd = False)

            if result.strip() == "":
                cmd = ["sh", "-c", oPB.OPB_PROD_UPDATER]
                result = self._processAction(cmd, action, ret, split = False, cwd = False)
            else:
                self.ret = ret
                self.rettype = oPB.MsgEnum.MS_ERR
                self.retmsg = translate("OpsiProcessing", "opsi package update process already running.")

        # ------------------------------------------------------------------------------------------------------------------------
        if action == oPB.OpEnum.DO_REBOOT:
            ret = oPB.RET_SSHCMDERR
            self.logger.ssh(20 * "-" + "ACTION: REBOOT DEPOT" + 20 * "-")

            result = self._processAction(oPB.OPB_REBOOT, action, ret, cwd = False)

        # ------------------------------------------------------------------------------------------------------------------------
        if action == oPB.OpEnum.DO_POWEROFF:
            ret = oPB.RET_SSHCMDERR
            self.logger.ssh(20 * "-" + "ACTION: POWEROFF DEPOT" + 20 * "-")

            result = self._processAction(oPB.OPB_POWEROFF, action, ret, cwd = False)

        # ------------------------------------------------------------------------------------------------------------------------
        if action == oPB.OpEnum.DO_GENMD5:
            ret = oPB.RET_SSHCMDERR
            packages = kwargs.get("packages", [])
            self.logger.ssh(20 * "-" + "ACTION: GENERATE MD5 CHECKSUMS" + 20 * "-")

            if packages:
                result = self._processAction(oPB.OPB_PRECHECK_MD5, action, ret, cwd = False)
                if result != {}:
                    packstring = (" ").join(packages)
                    cmd = ["sh", "-c", "PACKETS=\"" + packstring + "\"; " + oPB.OPB_CALC_MD5]
                    result = self._processAction(cmd, action, ret, split = False, cwd = False)

        # global return
        return self.ret, self.rettype, self.retmsg, result

    def _processAction(self, cmd, action, retval, split = True, cwd = True):
        """
        Process actual job execution

        :param cmd: command to execute, see ``spur.shell.run`` for details
        :param action: action, see ``oPB.OpEnum``
        :param retval: process errorcodes, see ``oPB.RET_**``
        :param split: optionally split ``cmd`` into parts (True) or not (False)
        :param cwd: change to working directory (``ControlFileData.path_on_server``) before command execution (True) or not (False)
        :return:
        """
        self.logger.sshinfo("Processing action...")

        self.reset_shell()

        if self.ret == oPB.RET_OK:

            if self._hook is None:
                # hook into stderr and stdout for progress analysis
                old_stderr = sys.stderr
                old_stdout = sys.stdout
                self._hook_stderr = AnalyseProgressHook(self, old_stderr, self.progressChanged)
                self._hook_stdout = AnalyseProgressHook(self, old_stdout, self.progressChanged)
                # sys.stdout = s

            # create a running marker for package creation
            if action in [oPB.OpEnum.DO_BUILD]:
                # create a running marker in project directory
                self.logger.sshinfo("Create process running marker file in project directory")
                f = tempfile.NamedTemporaryFile(prefix='process_', suffix=".run", dir=self._control.projectfolder)
                self.logger.sshinfo("File created: " + f.name)

            try:
                with self.shell:
                    try:
                        if split is True:
                            cmd = cmd.split()
                        if cwd is True:
                            cwdstr = self._control.path_on_server
                        else:
                            cwdstr = None

                        self.logger.sshinfo("Trying to execute command: " + self._obscurepass((" ").join(cmd)))

                        if action in [oPB.OpEnum.DO_INSTALL, oPB.OpEnum.DO_QUICKINST, oPB.OpEnum.DO_INSTSETUP,
                                      oPB.OpEnum.DO_UPLOAD, oPB.OpEnum.DO_UNINSTALL, oPB.OpEnum.DO_QUICKUNINST]:
                            self.logger.sshinfo("Start SSH shell with full environment update / pseudo TTY")
                            result = self.shell.run(
                                cmd,
                                cwd = cwdstr,
                                update_env = self._env,
                                allow_error = True,
                                stderr = self._hook_stderr,
                                stdout = self._hook_stdout,
                                use_pty = True,
                                encoding = 'UTF-8'
                            )

                        elif action in [oPB.OpEnum.DO_DEPLOY]:
                            self.logger.sshinfo("Start SSH shell with no environment update / pseudo TTY")
                            result = self.shell.run(
                                cmd,
                                cwd = cwdstr,
                                allow_error = True,
                                stderr = self._hook_stderr,
                                stdout = self._hook_stdout,
                                use_pty = True,
                                encoding = 'UTF-8'
                            )

                        elif action in [oPB.OpEnum.DO_GETREPOCONTENT, oPB.OpEnum.DO_GETCLIENTS, oPB.OpEnum.DO_GETPRODUCTS]:
                            self.logger.sshinfo("Start SSH shell with full environment update / no pseudo TTY / no stdout hook")
                            result = self.shell.run(
                                cmd,
                                cwd=cwdstr,
                                update_env=self._env,
                                allow_error=True,
                                stderr=self._hook_stderr,
                                encoding='UTF-8'
                            )

                        else:
                            self.logger.sshinfo("Start SSH shell with full environment update / no pseudo TTY")
                            result = self.shell.run(
                                cmd,
                                cwd = cwdstr,
                                update_env = self._env,
                                allow_error = True,
                                stderr = self._hook_stderr,
                                stdout = self._hook_stdout,
                                encoding = 'UTF-8'
                            )

                        # Log standard out / but not for clients and products, too much!!!
                        # and not for client agent deploy, sometimes there are errors in stdout, too
                        if action not in [oPB.OpEnum.DO_GETCLIENTS, oPB.OpEnum.DO_GETPRODUCTS, oPB.OpEnum.DO_GETDEPOTS,
                                          oPB.OpEnum.DO_GETCLIENTSONDEPOTS, oPB.OpEnum.DO_GETPRODUCTSONDEPOTS,
                                          oPB.OpEnum.DO_CREATEJOBS, oPB.OpEnum.DO_GETATJOBS, oPB.OpEnum.DO_GETREPOCONTENT,
                                          oPB.OpEnum.DO_DEPLOY, oPB.OpEnum.DO_GETGROUPS, oPB.OpEnum.DO_GETCLIENTGROUPS]:
                            out = Helper.strip_ansi_codes(result.output).splitlines()
                            for line in out:
                                line = self._obscurepass(line)
                                self.logger.ssh(line)

                        #if action in [oPB.OpEnum.DO_DEPLOY]:
                        #    out = Helper.strip_ansi_codes(result.output.decode(encoding='UTF-8')).splitlines()
                        #    isErr = self.hasErrors(out, self.logger.ssh)
                        #    if isErr[0]:
                        #        self.ret = retval
                        #        self.rettype = oPB.MsgEnum.MS_ERR
                        #        self.retmsg = isErr[1]

                        # Log standard error

                        # ONLY FOR DEBUGGING stdout: self.logger.info(Helper.strip_ansi_codes(result.output.decode(encoding = 'UTF-8')).splitlines())
                        # ONLY FOR DEBUGGING stderr: self.logger.info(Helper.strip_ansi_codes(result.stderr_output.decode(encoding = 'UTF-8')).splitlines())

                        self.logger.sshinfo("Return code from external command: " + str(result.return_code))

                        # sometimes the commands don't return anything via stderr, but the error msg is printed out via
                        # stdout, so first check, if we have anything in stderr, if not, analyse stdout
                        #
                        # first: see, if external command was ok (retcode = 0) or not (retcode != 0)
                        # if it is not, then search everywhere for any error message
                        if Helper.strip_ansi_codes(result.stderr_output).splitlines() == []:
                            out = Helper.strip_ansi_codes(result.output).splitlines()
                            # don't log stdout twice during error checking
                            logger = None
                        else:
                            out = Helper.strip_ansi_codes(result.stderr_output).splitlines()
                            # write stderr to log
                            logger = self.logger.ssh

                        # now actually, check for errors
                        # CAUTION: THERE MIGHT BE FALSE POSITTIVES BECAUSE OF FAULTY ERROR MESSAGE AND RETCODE HANDLING
                        # IN opsi-package-manager and opsi-makeproductfile
                        # ie. a package name like "Error management tool" could lead to a false positive

                        # DISABLE ERROR CHECKING FOR SPECIAL COMMANDS
                        # for now, disable error checking for some special commands, which will definitely lead into
                        # false positives
                        if not action in [oPB.OpEnum.DO_GETPRODUCTS]:
                            isErr = self.hasErrors(out, logger)
                            if isErr[0]:
                                self.ret = retval
                                self.rettype = oPB.MsgEnum.MS_ERR
                                self.retmsg = isErr[1]

                    except spur.NoSuchCommandError:
                        self.logger.error("Set return code to RET_SSHCMDERR")
                        self.logger.error("Command not found. See Log for details: " + self._obscurepass((" ").join(cmd)))
                        self.ret = oPB.RET_SSHCMDERR
                        self.rettype = oPB.MsgEnum.MS_ERR
                        self.retmsg = translate("OpsiProcessing", "Command not found. See Log for details.")
                        return {}

                    except spur.ssh.ConnectionError as error:
                            self.logger.error(repr(error).replace("\\n"," --> "))
                            self.logger.error("Set return code to RET_SSHCONNERR")
                            self.ret = oPB.RET_SSHCONNERR
                            self.rettype = oPB.MsgEnum.MS_ERR
                            self.retmsg = translate("OpsiProcessing", "Error establishing SSH connection. See Log for details.")
                            return {}

            except ConnectionError as error:
                    self.logger.error(repr(error).replace("\\n"," --> "))
                    self.logger.error("Set return code to RET_SSHCONNERR")
                    self.ret = oPB.RET_SSHCONNERR
                    self.rettype = oPB.MsgEnum.MS_ERR
                    self.retmsg = translate("OpsiProcessing", "Error establishing SSH connection. See Log for details.")
                    return {}

            # reset hook state
            #sys.stdout = old_stderr

            # remove running marker
            if action in [oPB.OpEnum.DO_BUILD]:
                self.logger.sshinfo("Removing process running marker file")
                f.flush()
                f.close()

            return result.output
        else:
            return {}

    def _obscurepass(self, line: str) ->str:
        """
        Try to find current SSH password in string and replace it with ***SECRET***

        Works only for first occurence of SSH password in ``line`` at the moment!!!

        :param line: string to scan
        :return: string with obscured password
        """
        if line.find(self._sshpass) != -1:
            try:
                hide = [line.find(self._sshpass), line.find(self._sshpass) + len(self._sshpass)]
                return line[:hide[0]] + "***SECRET***" + line[hide[1]:]
            except:
                return line
        else:
            return line

    def hasErrors(self, text: list, info_logger) -> bool:
        """
        Check ``text`` for some common error messages in output after SSH command execution

        List is far from being complete ;-)

        :param text: text to scan
        :return: errors found (True) or not (False)
        """

        found = False
        msg = []

        for line in text:
            if line.strip() == '':
                continue

            line = self._obscurepass(line)

            if "ERROR: 'ascii' codec can't encode character".upper() in line.upper():
                self.logger.error(line)
                found = True
                msg.append(translate("OpsiProcessing", "There are umlauts in some fields which can't be processed: build failed."))

            if "Backend error: Failed to install package".upper() in line.upper():
                self.logger.error(line)
                found = True
                msg.append(translate("OpsiProcessing", "Backend error: installation failed."))

            if "ERROR: Failed to process command 'install'".upper() in line.upper():
                self.logger.error(line)
                found = True
                msg.append(translate("OpsiProcessing", "Could not process 'install' command."))

            if "ERROR: Package file".upper() in line.upper():
                self.logger.error(line)
                found = True
                msg.append(translate("OpsiProcessing", "Package file error. Check log."))

            if "ERROR: Failed to process command 'extract':".upper() in line.upper():
                self.logger.error(line)
                found = True
                msg.append(translate("OpsiProcessing", "Error during package extraction. Check log."))

            if "opsi-makeproductfile: Permission denied".upper() in line.upper():
                self.logger.error(line)
                found = True
                msg.append(translate("OpsiProcessing", "Permission denied during opsi-makeproductfile. Check log or raise log level for opsi-makeproductfile."))

            if "Permission denied:".upper() in line.upper():
                self.logger.error(line)
                found = True
                msg.append(translate("OpsiProcessing", "Permission denied. Check log."))

            if "NT_STATUS_LOGON_FAILURE".upper() in line.upper():
                self.logger.error(line)
                found = True
                msg.append(translate("OpsiProcessing", "Network logon failed. Check log."))

            if "not found".upper() in line.upper():
                self.logger.error(line)
                found = True
                msg.append(translate("OpsiProcessing", "Possible error: command or file not found. Check log."))

            if "Failed to get ip address for host".upper() in line.upper():
                self.logger.error(line)
                found = True
                msg.append(translate("OpsiProcessing", "Failed to get ip address for host. Check log."))

            if "Failed to execute".upper() in line.upper():
                self.logger.error(line)
                found = True
                msg.append(translate("OpsiProcessing", "Failed to execute opsi command. Check log."))

            if "currently locked on depot".upper() in line.upper():
                self.logger.error(line)
                found = True
                msg.append(translate("OpsiProcessing", "The requested product action is not possible, because it is currently locked on the server. Check log."))

            # search for the word "ERROR", but only, if there is no previous error
            # and not, if line contains:
            # "INFO: ADDING FILE" <- opsi-makeproductfile output
            if ("ERROR".upper() in line.upper()) and \
                    not ("INFO: ADDING FILE".upper() in line.upper()) and \
                    (found == False):
                self.logger.error(line)
                found = True
                msg.append(translate("OpsiProcessing", "Undefined error occurred. Check log."))

            if not found and not info_logger is None:
                info_logger(line)

        ret = []
        [ret.append(i) for i in msg if not i in ret]

        return (found, '\n'.join(ret))

    def copyToRemote(self, localfile: object, remotefile: str, localopen: bool = False):
        """
        Copy local file to remote destination viy SSH connection

        :param localfile: local file name / local file handle (``localopen`` = True)
        :param remotefile: destination file name
        :param localopen: specify, if local file is already opened or not
        """
        self.reset_shell()
        self.logger.debug("Copying file: %s (local) to %s (remote)", localfile, remotefile)
        try:
            if localopen == False:
                with open(localfile, "rb") as local_file:
                    with self.shell.open(remotefile, "wb") as remote_file:
                        shutil.copyfileobj(local_file, remote_file)
            else:
                with self.shell.open(remotefile, "wb") as remote_file:
                    shutil.copyfileobj(localfile, remote_file)

        except Exception as error:
            self.logger.error("Error while copying file: " + repr(error))


    def reset_shell(self):
        """Clear _shell object"""
        if self._shell is not None:
            self._shell = None

    @property
    def shell(self):
        """
        Dispatch ssh shell creation depending on online status and configuration value

        :return: spur shell object
        """
        if self._shell is None:
            if self.ip.startswith("127."):
                self._shell = self._shell_local()
            elif ConfigHandler.cfg.usekeyfile == "True":
                self._shell = self._shell_remote_with_keyfile()
            else:
                self._shell = self._shell_remote_with_password()
        return self._shell

    @property
    def ip(self):
        """
        Check if server ip from configuration is the same as our local ip

        :return: server ip to use
        """

        if self._ip == None:
            local = self._get_local_ip()
            try:
                remote = socket.gethostbyname(self._server)
                if local == remote:
                    self._ip = '127.0.0.1'
                else:
                    self._ip = remote
            except:
                self._ip = "0.0.0.0"
        return self._ip

    def _get_local_ip(self):
        """
        Try to get local network ip address.

        :return: current ip address or "127.0.0.1"
        """
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            # doesn't even have to be reachable
            s.connect(('10.255.255.255', 0))
            IP = s.getsockname()[0]
        except:
            IP = '127.0.0.1'
        finally:
            s.close()
        return IP

    def _shell_remote_with_keyfile(self):
        """
        Establishing ssh shell via private keyfile authorization

        :return: spur shell object
        """
        self.logger.debug("Establishing ssh shell via private keyfile authorization")
        self.logger.debug("Server IP: " + self.ip)
        self.logger.debug("Port no: " + self._sshport)
        self.logger.debug("Username: " + self._sshuser)
        shell = spur.SshShell(hostname=self.ip, port=int(self._sshport), username=self._sshuser, private_key_file=self._sshkey, connect_timeout=15,
                              missing_host_key=self._hostkey_policy, look_for_private_keys= False)
        return shell

    def _shell_remote_with_password(self):
        """
        Establishing ssh shell via user/password authorization

        :return: spur shell object
        """
        self.logger.debug("Establishing ssh shell via user/password authorization")
        self.logger.debug("Server IP: " + self.ip)
        self.logger.debug("Port no: " + self._sshport)
        self.logger.debug("Username: " + self._sshuser)
        shell = spur.SshShell(hostname=self.ip, port=int(self._sshport), username=self._sshuser, password=self._sshpass, connect_timeout=15,
                              missing_host_key=self._hostkey_policy, look_for_private_keys= False)
        return shell

    def _shell_local(self):
        """
        Establishing local shell

        :return: spur shell object
        """
        self.logger.debug("Establishing local shell")
        shell = spur.LocalShell()
        return shell


class AnalyseProgressHook(StringIO):
    """
    Redirect hook to anaylse stderr from spur.shell.run.

    based on this idea: http://bulkan-evcimen.com/redirecting-stdout-to-stringio-object.html
    """
    def __init__(self, parent, channel, signal):
        """
        Constructor of AnalyseProgressHook

        :param parent: parent object
        :param channel: stderr or stdout to analyse
        :param signal: signal with which the message is dispatched
        """
        self._match = re.compile('\s*(\d*\.?\d*)')
        self.__channel = channel
        self._parent = parent
        self._signal = signal
        self._line = ""
        StringIO.__init__(self)

    def write(self, s):
        """
            "Misuse" write() to capture a whole line and analyse its content

        :param s: output byte
        """

        try:

            # receive single bytes, concert to string and
            # concatenate them to a whole line
            if type(s) == bytes:
                s = s.decode('utf-8')
            if s == '\n':
                self._line = ""
            else:
                self._line += s

            if self._line.strip():  # strip out empty lines
                m = self._match.search(self._line)
                if m:
                    if m.group(0).strip() != "":
                        count = float(m.group(0).strip())
                        progress = translate("ProgressHook", "In progress:") + " " + '\r[{0}] {1}%'.format('=' * int(count/5), count)
                        self._signal.emit(progress)
                        self.__channel.write(progress)


        # eol throws a unicode decode error, so simply ignore it
        except UnicodeDecodeError:
            pass

    def read(self):
        """Read current stream and write to __channel"""
        self.seek(0)
        self.__channel.write(StringIO.read(self))

