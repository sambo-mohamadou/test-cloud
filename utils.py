import os
import ssl
import sys
import tarfile
from threading import Timer
from urllib.request import Request, urlopen
from pyVim.connect import SmartConnect
from pyVmomi import vim, vmodl



def connect():
    """
    Connexion to ESXi/vCenter. 
     - host: the IP or hostname of the ESXi/vCenter
     - user: ESXi/vCenter user
     - pwd: ESXi/vCenter password
    """
    service_instance = SmartConnect(host="192.168.149.128", user="root", pwd="toto32**", disableSslCertValidation=True)
    # return service_instance.RetrieveServiceContent()
    return service_instance

def get_vm(content, vm_name = None):
    """
    Get the VM from its name
     - content: the vmWare ServiceContent
     - vm_name (optional): the name of the VM to retrieve
    """
    if vm_name is None:
        vm_name = "ouups"
    container = content.rootFolder
    view_type = [vim.VirtualMachine]
    recursive = True

    container_view = content.viewManager.CreateContainerView(
        container, view_type, recursive
    )
    vm_list = container_view.view
    for vm in vm_list:
        if vm.name == vm_name:
            return vm
        

def get_dc(si, name):
    """
    Get a datacenter by its name.
    """
    for datacenter in si.content.rootFolder.childEntity:
        if datacenter.name == name:
            return datacenter
    raise Exception('Failed to find datacenter named %s' % name)


def get_rp(si, datacenter, name):
    """
    Get a resource pool in the datacenter by its names.
    """
    view_manager = si.content.viewManager
    container_view = view_manager.CreateContainerView(datacenter, [vim.ResourcePool], True)
    try:
        for resource_pool in container_view.view:
            if resource_pool.name == name:
                return resource_pool
    finally:
        container_view.Destroy()
    raise Exception("Failed to find resource pool %s in datacenter %s" %
                    (name, datacenter.name))


def get_largest_free_rp(si, datacenter):
    """
    Get the resource pool with the largest unreserved memory for VMs.
    """
    view_manager = si.content.viewManager
    container_view = view_manager.CreateContainerView(datacenter, [vim.ResourcePool], True)
    largest_rp = None
    unreserved_for_vm = 0
    try:
        for resource_pool in container_view.view:
            if resource_pool.runtime.memory.unreservedForVm > unreserved_for_vm:
                largest_rp = resource_pool
                unreserved_for_vm = resource_pool.runtime.memory.unreservedForVm
    finally:
        container_view.Destroy()
    if largest_rp is None:
        raise Exception("Failed to find a resource pool in datacenter %s" % datacenter.name)
    return largest_rp


def get_ds(datacenter, name):
    """
    Pick a datastore by its name.
    """
    for datastore in datacenter.datastore:
        try:
            if datastore.name == name:
                return datastore
        except Exception:  # Ignore datastores that have issues
            pass
    raise Exception("Failed to find %s on datacenter %s" % (name, datacenter.name))


def get_largest_free_ds(datacenter):
    """
    Pick the datastore that is accessible with the largest free space.
    """
    largest = None
    largest_free = 0
    for datastore in datacenter.datastore:
        try:
            free_space = datastore.summary.freeSpace
            if free_space > largest_free and datastore.summary.accessible:
                largest_free = free_space
                largest = datastore
        except Exception:  # Ignore datastores that have issues
            pass
    if largest is None:
        raise Exception('Failed to find any free datastores on %s' % datacenter.name)
    return largest

def get_tarfile_size(tarfile):
    """
    Determine the size of a file inside the tarball.
    If the object has a size attribute, use that. Otherwise seek to the end
    and report that.
    """
    if hasattr(tarfile, 'size'):
        return tarfile.size
    size = tarfile.seek(0, 2)
    tarfile.seek(0, 0)
    return size

class OvfHandler(object):
    """
    OvfHandler handles most of the OVA operations.
    It processes the tarfile, matches disk keys to files and
    uploads the disks, while keeping the progress up to date for the lease.
    """
    def __init__(self, ovafile):
        """
        Performs necessary initialization, opening the OVA file,
        processing the files and reading the embedded ovf file.
        """
        self.handle = self._create_file_handle(ovafile)
        self.tarfile = tarfile.open(fileobj=self.handle)
        ovffilename = list(filter(lambda x: x.endswith(".ovf"),
                                  self.tarfile.getnames()))[0]
        ovffile = self.tarfile.extractfile(ovffilename)
        self.descriptor = ovffile.read().decode()

    def _create_file_handle(self, entry):
        """
        A simple mechanism to pick whether the file is local or not.
        This is not very robust.
        """
        return FileHandle(entry)


    def get_descriptor(self):
        return self.descriptor

    def set_spec(self, spec):
        """
        The import spec is needed for later matching disks keys with
        file names.
        """
        self.spec = spec

    def get_disk(self, file_item):
        """
        Does translation for disk key to file name, returning a file handle.
        """
        ovffilename = list(filter(lambda x: x == file_item.path,
                                  self.tarfile.getnames()))[0]
        return self.tarfile.extractfile(ovffilename)

    def get_device_url(self, file_item, lease):
        for device_url in lease.info.deviceUrl:
            if device_url.importKey == file_item.deviceId:
                return device_url
        raise Exception("Failed to find deviceUrl for file %s" % file_item.path)

    def upload_disks(self, lease, host):
        """
        Uploads all the disks, with a progress keep-alive.
        """
        self.lease = lease
        try:
            self.start_timer()
            for fileItem in self.spec.fileItem:
                self.upload_disk(fileItem, lease, host)
            lease.Complete()
            print("Finished deploy successfully.")
            return 0
        except vmodl.MethodFault as ex:
            print("Hit an error in upload: %s" % ex)
            lease.Abort(ex)
        except Exception as ex:
            print("Lease: %s" % lease.info)
            print("Hit an error in upload: %s" % ex)
            lease.Abort(vmodl.fault.SystemError(reason=str(ex)))
        return 1

    def upload_disk(self, file_item, lease, host):
        """
        Upload an individual disk. Passes the file handle of the
        disk directly to the urlopen request.
        """
        ovffile = self.get_disk(file_item)
        if ovffile is None:
            return
        device_url = self.get_device_url(file_item, lease)
        url = device_url.url.replace('*', host)
        headers = {'Content-length': get_tarfile_size(ovffile)}
        if hasattr(ssl, '_create_unverified_context'):
            ssl_context = ssl._create_unverified_context()
        else:
            ssl_context = None
        req = Request(url, ovffile, headers)
        urlopen(req, context=ssl_context)

    def start_timer(self):
        """
        A simple way to keep updating progress while the disks are transferred.
        """
        Timer(5, self.timer).start()

    def timer(self):
        """
        Update the progress and reschedule the timer if not complete.
        """
        try:
            prog = self.handle.progress()
            self.lease.Progress(prog)
            if self.lease.state not in [vim.HttpNfcLease.State.done,
                                        vim.HttpNfcLease.State.error]:
                self.start_timer()
            sys.stderr.write("Progress: %d%%\r" % prog)
        except Exception:  # Any exception means we should stop updating progress.
            pass


class FileHandle(object):
    def __init__(self, filename):
        self.filename = filename
        self.fh = open(filename, 'rb')

        self.st_size = os.stat(filename).st_size
        self.offset = 0

    def __del__(self):
        self.fh.close()

    def tell(self):
        return self.fh.tell()

    def seek(self, offset, whence=0):
        if whence == 0:
            self.offset = offset
        elif whence == 1:
            self.offset += offset
        elif whence == 2:
            self.offset = self.st_size - offset

        return self.fh.seek(offset, whence)

    def seekable(self):
        return True

    def read(self, amount):
        self.offset += amount
        result = self.fh.read(amount)
        return result

    # A slightly more accurate percentage
    def progress(self):
        return int(100.0 * self.offset / self.st_size)