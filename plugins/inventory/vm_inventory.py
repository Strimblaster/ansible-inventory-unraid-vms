from ansible.plugins.inventory import BaseInventoryPlugin

import re

DOCUMENTATION = '''
    name: vm_inventory
    short_description: Inventory source for VMs in Unraid
    description:
        - Gets VMs hosted in an Unraid machine.
        - This plugin fetches the list of VMs from Unraid and gets the IPv4 address of each VM. No IPv6 support.
        - The plugin requires the 'paramiko' package to be installed.
    version_added: "2.16"   
    author:
        - Emanuel Ferreira (@Strimblaster)
    options:
        unraid_host:
            description: Unraid Hostname or IP address
            required: True
        unraid_user:
            description: Unraid SSH User
            required: True
        unraid_password:
            description: Unraid SSH Password
            required: True
        vm_name_pattern:
            description: |
                Regex pattern that determines which VMs should be added in the inventory based on their name.
                By default, all VMs are added.
            type: string
            required: False
            default: '.*'
        vm_interface_pattern:
            description: |
                Name of the network interface to get the IP address from. 
                If multiple interfaces are found, the first ethernet with IPv4 is used.
            type: string
            required: False
            default: 'en\w+'
        ansible_user:
            description: |
                General user to be used for SSH connections to the VMs. None by default.
            type: string
            required: False
            default: None
    requirements:
        - python >= 3.10
        - paramiko
'''




class InventoryModule(BaseInventoryPlugin):
    NAME = "vm_inventory"

    def verify_file(self, path):
        valid = False
        if super(InventoryModule, self).verify_file(path):
            if path.endswith(("unraid_vm_inventory.yml", "unraid_vm_inventory.yaml")):
                valid = True
        return valid

    def parse(self, inventory, loader, path, cache=True):
        from ansible.errors import AnsibleError
        
        try:
            import paramiko
        except ImportError:
            raise AnsibleError("The 'paramiko' package is required for this inventory plugin")

        # call base method to ensure properties are available for use with other helper methods
        super(InventoryModule, self).parse(inventory, loader, path, cache)

        # Read the inventory YAML file
        print(f"Reading inventory from {path}")
        config = self._read_config_data(path)
        hostname = config.get("unraid_host")
        username = config.get("unraid_user")
        password = config.get("unraid_password")
        name_pattern = config.get("vm_name_pattern") or ".*"
        vm_interface_pattern = config.get("vm_interface_pattern") or "en\w+"
        ansible_user = config.get("ansible_user")
        if not all([hostname, username, password]):
            raise AnsibleError("Missing required parameters")
        print(f"Reading VMs from {hostname}")

        # Initialize SSH connection to Unraid
        from paramiko import SSHClient
        client = SSHClient()
        client.load_system_host_keys()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        print(f"Connecting to {hostname} as {username}")
        client.connect(hostname=hostname, username=username, password=password)

        # List VMs from Unraid
        stderr = None
        try:
            stdin, stdout, stderr = client.exec_command("virsh list --all --name")
            vms: list[str] = stdout.read().decode().splitlines()
            print(f"Found VMs: {vms}")
        except Exception as e:
            msg = f"Error listing VMs: {e}. {stderr.read().decode() if stderr else ''}"
            raise AnsibleError(msg)

        # Filter VMs based on the name pattern
        vms = [vm for vm in vms if re.match(name_pattern, vm)]
        print(f"Filtered VMs based on '{name_pattern}' pattern : {vms}")

        if not vms:
            print("No VMs found")
            return

        # Get the IP address of each VM
        vms_ips = {}

        for vm in vms:
            try:
                stdin, stdout, stderr = client.exec_command(f"virsh domifaddr '{vm}' --source agent")
            except Exception as e:
                raise AnsibleError(f"Error getting IP for VM '{vm}': {e}. {stderr.read().decode() if stderr else ''}")
            
            try:
                lines = stdout.read().decode().splitlines()
                ip: str | None = self._parse_virsh_domifaddr(lines, vm_interface_pattern)
                if ip:
                    print(f"Found IP '{ip}' for VM '{vm}'")
                    vms_ips[vm] = ip
                else:
                    print(f"Could not find IP for VM '{vm}'. Does this machine have QEMU agent installed?")
            except Exception as e:
                print(f"Error parsing IP for VM '{vm}': {e}")
                continue


        # Add VMs to the inventory
        if vms_ips:
            self.inventory.add_group("unraid")
        for vm, ip in vms_ips.items():
            vm = vm.replace(" ", "_").replace("-", "_").lower()
            vm = re.sub(r"\W", "", vm)
            vm = re.sub(r"_+", "_", vm)
            print(f"Adding VM '{vm}' with IP '{ip}' to inventory")
            self.inventory.add_host(vm, group="unraid")
            self.inventory.set_variable(vm, "ansible_host", ip)
            if ansible_user:
                self.inventory.set_variable(vm, "ansible_user", ansible_user)

        client.close()
        print(f"Added {len(vms_ips)} VMs to inventory")

    def _parse_virsh_domifaddr(self, lines: list[str], interface_pattern: str) -> str | None:

        """
        Sample command output:
         Name       MAC address          Protocol     Address
        -------------------------------------------------------------------------------
         lo         00:00:00:00:00:00    ipv4         127.0.0.1/8
         -          -                    ipv6         ::1/128
         enp1s0     52:54:00:00:f0:f9    ipv4         192.168.1.86/24
         -          -                    ipv6         fe80::5054:ff:fef0:f9df/64
        """
        
        for line in lines:
            splitted_line = line.split()
            if "ipv4" in line and re.match(interface_pattern, splitted_line[0]):
                return splitted_line[-1].split("/")[0]
        return None
