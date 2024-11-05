import json

from utils import connect, get_dc, get_ds, get_largest_free_ds, get_largest_free_rp, get_rp, get_vm
from pyVmomi import vim


def main():
    with open("clone_config.json") as file:
        args = json.load(file)
    
    si = connect()

    if args["datacenter_name"]:
        datacenter = get_dc(si, args["datacenter_name"])
    else:
        datacenter = si.content.rootFolder.childEntity[0]

    if args["resource_pool"]:
        resource_pool = get_rp(si, datacenter, args["resource_pool"])
    else:
        resource_pool = get_largest_free_rp(si, datacenter)

    if args["datastore_name"]:
        datastore = get_ds(datacenter, args["datastore_name"])
    else:
        datastore = get_largest_free_ds(datacenter)


    if args["vm_name"]:
        vmToClone = get_vm(si.RetrieveContent(), args["vm_name"])
    else:
        print("Veuillez preciser le nom de la machine virtuelle !!!!!!!")
        return

    vmFolder = datacenter.vmFolder

    relospec = vim.vm.RelocateSpec()
    relospec.datastore = datastore
    relospec.pool = resource_pool

    clonespec = vim.vm.CloneSpec()
    clonespec.location = relospec
    clonespec.powerOn = True
    

    cloneName = vmToClone.name + " Clone"

    print("cloning")

    print(vmToClone.permission)
    # vmToClone.CloneVM_Task(folder=vmFolder, name=cloneName, spec=clonespec)

    print("Done !")
    

# Start program
if __name__ == "__main__":
    main()