import json
import sys
import time

from utils import OvfHandler, connect, get_dc, get_ds, get_largest_free_ds, get_largest_free_rp, get_rp

from pyVmomi import vim

def main():
    # parser = cli.Parser()
    # parser.add_optional_arguments(cli.Argument.OVA_PATH, cli.Argument.DATACENTER_NAME,
    #                               cli.Argument.RESOURCE_POOL, cli.Argument.DATASTORE_NAME)
    #
    with open("config.json") as file:
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

    ovf_handle = OvfHandler(args["ova_path"])
    

    ovf_manager = si.content.ovfManager
    # CreateImportSpecParams can specify many useful things such as
    # diskProvisioning (thin/thick/sparse/etc)
    # networkMapping (to map to networks)
    # propertyMapping (descriptor specific properties)
    cisp = vim.OvfManager.CreateImportSpecParams()
    cisr = ovf_manager.CreateImportSpec(
        ovf_handle.get_descriptor(), resource_pool, datastore, cisp)

    # These errors might be handleable by supporting the parameters in
    # CreateImportSpecParams
    if cisr.error:
        print("The following errors will prevent import of this OVA:")
        for error in cisr.error:
            print("%s" % error)
        return 1

    ovf_handle.set_spec(cisr)

    lease = resource_pool.ImportVApp(cisr.importSpec, datacenter.vmFolder)
    while lease.state == vim.HttpNfcLease.State.initializing:
        print("Waiting for lease to be ready...")
        time.sleep(1)

    if lease.state == vim.HttpNfcLease.State.error:
        print("Lease error: %s" % lease.error)
        return 1
    if lease.state == vim.HttpNfcLease.State.done:
        return 0

    print("Starting deploy...")
    return ovf_handle.upload_disks(lease, args["host"])


if __name__ == "__main__":
    sys.exit(main())