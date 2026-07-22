
#####################
# This automation script still have idempotence issues, ensure resources from previous runs are properly removed before running to prevent unintended errors

# App Service Plan has throttling behavior for repeated remove/create actions, consider creating a separate persistent rg and reuse it between run
######################
import os
import ipaddress

from util import run_command

APP_SERVICE_SKU = 'B1'
APP_SERVICE_LOCATION = "centralus"
APP_SERVICE_PRIVATE_DNS_ZONE = "privatelink.azurewebsites.net"

LOCATION = 'canadaeast'

RG_SHARED = 'p1-rg-shared-test'
RG_P1 = 'p1-rg-test'

ACR_NAME = 'p1acrtrio'
ACR_SKU = 'Basic'

LOG_ANALYTICS_WORKSPACE_NAME = "p1-log-analytics"
LOG_ANALYTICS_SKU = "PerGB2018"
LOG_ANALYTICS_RETENTION_DAYS = "30"

MANAGED_IDENTITY_ACR_CONFIG = '{"acrUseManagedIdentityCreds": true}'

PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))
)
SSH_KEY_PATH = os.path.expanduser("~/.ssh/id_rsa")
BOOTSTRAP_SCRIPT = os.path.join(
    PROJECT_ROOT, 
    "app", 
    "bootstrap_vm.sh"
)
WEB_APP_DIR = os.path.join(
    PROJECT_ROOT,
    "azure",
    "web-app"
)


def authenticate():
    # Authenticate the python automation script with azure using service principal
    # Can be skipped in run manually from local machine
    pass


def create_resource_group(rg_name):

    # Resource group can contain resources in different regions
    # For the purpose of this project, using all resource groups could be in one region for simplicity
    create_rg_cmd = [
        "az", "group", "create", 
        "--name", rg_name, 
        "--location", LOCATION, 
        "--output", "table"
    ]
    run_command(create_rg_cmd)

def create_vnet_if_not_exists(rg_name, vnet_name, location, address_prefix):
    
    check_vnet_name_cmd = [
        "az", "network", "vnet", "list",
        "--resource-group", rg_name,
        "--query", f"[?name=='{vnet_name}'].name",
        "--output", "tsv",
    ]
    vnet_exists = run_command(check_vnet_name_cmd)

    if not vnet_exists:
        create_vnet_cmd = [
            "az", "network", "vnet", "create",
            "--resource-group", rg_name,
            "--name", vnet_name,
            "--location", location,
            "--address-prefixes", address_prefix
        ]
        run_command(create_vnet_cmd)
    else:
        print(f"VNet {vnet_name} already exists in region {location}")

def create_subnet(rg_name, vnet_name, subnet_name, subnet_prefix):
    # NEED ADDITIONAL CHECK FOR IDEMPOTENCY
    
    subnet_create_cmd = [
        "az", "network", "vnet", "subnet", "create",
        "--resource-group", rg_name,
        "--vnet-name", vnet_name,
        "--name", subnet_name,
        "--address-prefixes", subnet_prefix
    ]
    run_command(subnet_create_cmd)


def create_vm_if_not_exists(
        rg_name, 
        vm_name,
        vnet_name,
        location, 
        port="8081",
        shutdown_time="2100"
    ):

    nsg_name = f"{vm_name}-nsg"
    public_ip_name = f"{vm_name}-pip"
    os_disk_name = f"{vm_name}-osdisk"

    check_vm_cmd = ["az", "vm", "list", "-g", rg_name, "--query", f"[?name=='{vm_name}'].name", "-o", "tsv"]
    vm_check_output = run_command(check_vm_cmd).strip()

    if not vm_check_output:
        print(f"VM {vm_name} not found. Provisioning now...")
        create_vm_cmd = [
            "az", "vm", "create",
            "--resource-group", rg_name,
            "--name", vm_name,

            "--image", "Ubuntu2204",
            "--size", "Standard_B2ats_v2",
            "--storage-sku", "Standard_LRS",

            "--location", location,

            "--admin-username", "azureuser",
            "--generate-ssh-keys",

            "--nsg", nsg_name,
            "--vnet-name", vnet_name,
            "--public-ip-address", public_ip_name,
            "--os-disk-name", os_disk_name,

            "--boot-diagnostics-storage", "",
            "--output", "table"
        ]
        run_command(create_vm_cmd)

    auto_shutdown_cmd = [
        "az", "vm", "auto-shutdown", 
        "-g", rg_name, 
        "-n", vm_name, 
        "--time", shutdown_time,
        "-o", "none"]
    run_command(auto_shutdown_cmd)

    create_nsg_cmd = [
        "az", "network", "nsg", "rule", "create",
        "--resource-group", rg_name,
        "--nsg-name", nsg_name,
        "--name", "Allow_8081_Inbound",
        "--priority", "1010",
        "--destination-port-ranges", port,
        "--direction", "Inbound",
        "--access", "Allow",
        "--protocol", "Tcp",
        "--description", "Allow FastAPI web traffic on port 8081",
        "--output", "table"
    ]
    run_command(create_nsg_cmd)

def deploy_app_to_vm_via_bootstrap_script(
        rg_name,
        vm_name,
        ssh_key_path, 
        source_bootstrap_path
    ):
    
    get_vm_public_ip_cmd = [
        "az", "vm", "list-ip-addresses",
        "-g", rg_name,
        "-n", vm_name,
        "--query", "[0].virtualMachine.network.publicIpAddresses[0].ipAddress",
        "-o", "tsv"
    ]
    vm_public_ip = run_command(get_vm_public_ip_cmd).strip().replace("\r", "")

    # SCP bootstrap script to remote VM
    print(f"=== Copying Bootstrap Script to Remote VM ({vm_public_ip}) ===")
    scp_cmd = [
        "scp",
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-i", ssh_key_path,
        source_bootstrap_path,
        f"azureuser@{vm_public_ip}:~/"
    ]
    run_command(scp_cmd)
    print()

    # SSH and run the remote bootstrap script to set up Docker, Docker Compose, and deploy the FastAPI application
    print("=== SSH into VM and Execute Bootstrap Script ===")
    ssh_cmd = [
        "ssh",
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-i", ssh_key_path,
        f"azureuser@{vm_public_ip}",
        "sudo bash ~/bootstrap_vm.sh"
    ]
    run_command(ssh_cmd, print_result=False)
    print()


def create_acr_if_not_exists(rg_name, acr_name, sku):

    register_provider_cmd = [
        "az", "provider", "register",
        "--namespace", "Microsoft.ContainerRegistry",
        "--wait"
    ]
    run_command(register_provider_cmd)

    check_acr_name_cmd = [
        "az", "acr", "check-name",
        "--name", acr_name,
        "--query", "nameAvailable",
        "-o", "tsv"
    ]

    name_available = run_command(check_acr_name_cmd)
    if name_available:        
        acr_cmd = [
            "az", "acr", "create",
            "--resource-group", rg_name,
            "--name", acr_name,
            "--sku", sku
        ]
        run_command(acr_cmd)

def build_locally_and_push_image_to_acr(
        acr_name, 
        image_name,
        image_tag="latest"
    ):
    
    acr_login_cmd = [
        "az", "acr", "login",
        "--name", acr_name
    ]
    run_command(acr_login_cmd)

    acr_show_login_server_cmd = [
        "az", "acr", "show",
        "--name", acr_name,
        "--query", "loginServer",
        "--output", "tsv"
    ]
    login_server = run_command(acr_show_login_server_cmd).strip()
    container_image = f"{login_server}/{image_name}:{image_tag}"

    docker_build_cmd = [
        "docker", "build",
        "-t", container_image,
        WEB_APP_DIR
    ]
    run_command(docker_build_cmd)

    docker_push_cmd = [
        "docker", "push",
        container_image,
    ]
    run_command(docker_push_cmd)

    return container_image


def create_app_service_plan_if_not_exists(
    app_service_plan_name, 
    rg_name,
    location,
    sku
):

    check_appservice_name_cmd = [
        "az", "appservice", "plan", "list",
        "--query", f"[?contains(name, '{app_service_plan_name}')].name",
        "-o", "tsv"
    ]
    appservice_plan_exists = run_command(check_appservice_name_cmd)

    if not appservice_plan_exists:
        appservice_plan_create_cmd = [
            "az", "appservice", "plan", "create",
            "--name", app_service_plan_name,
            "--resource-group", rg_name,
            "--location", location,
            "--sku", sku,
            "--is-linux"
        ]
        run_command(appservice_plan_create_cmd)
    else:
        print(f"App Service Plan {app_service_plan_name} exists within resource group {rg_name}")

def create_empty_web_app_with_managed_identity(
        rg_name, 
        location, 
        app_service_plan_name,
        web_app_name, 
        container_image,
        sku=APP_SERVICE_SKU
    ):
    
    create_app_service_plan_if_not_exists(app_service_plan_name, RG_SHARED, location, sku)

    web_app_create_cmd = [
        "az", "webapp", "create",
        "--resource-group", rg_name,
        "--plan", app_service_plan_name,
        "--name", web_app_name,
        "--container-image-name", container_image,
        "--output", "none"
    ]
    run_command(web_app_create_cmd)

    identity_assign_cmd = [
        "az", "webapp", "identity", "assign",
        "--resource-group", rg_name,
        "--name", web_app_name,
        "--output", "none"
    ]
    run_command(identity_assign_cmd)

    webapp_enable_acr_managed_identity_cmd = [
        "az", "webapp", "config", "set",
        "--resource-group", rg_name,
        "--name", web_app_name,
        "--generic-configurations", MANAGED_IDENTITY_ACR_CONFIG,
    ]
    run_command(webapp_enable_acr_managed_identity_cmd)

def deploy_container_to_azure_web_app(
        rg_name, 
        acr_name, 
        location, 
        app_service_plan_name, 
        web_app_name, 
        appservice_sku,
        image_name,
        image_tag,
        container_port,
        acr_sku
    ):
    # Create an ACR
    # Deploy as a quick task
        # Utilize az acr build to let ACR build and push image to the ACR
        # Create Azure Web App pointing to the image on the ACR 


    create_acr_if_not_exists(RG_SHARED, acr_name, acr_sku)
    container_image = build_locally_and_push_image_to_acr(acr_name, image_name, image_tag)

    create_empty_web_app_with_managed_identity(rg_name, location, app_service_plan_name, web_app_name, container_image, appservice_sku)

    webapp_identity_show_cmd = [
        "az", "webapp", "identity", "show",
        "--resource-group", rg_name,
        "--name", web_app_name,
        "--query", "principalId",
        "--output", "tsv"
    ]
    principal_id = run_command(webapp_identity_show_cmd).strip()

    acr_show_id_cmd = [
        "az", "acr", "show",
        "--name", acr_name,
        "--query", "id",
        "--output", "tsv"
    ]
    acr_id = run_command(acr_show_id_cmd).strip()

    acr_pull_role_assignment_cmd = [
        "az", "role", "assignment", "create",
        "--assignee-object-id", principal_id,
        "--assignee-principal-type", "ServicePrincipal",
        "--role", "AcrPull",
        "--scope", acr_id
    ]
    run_command(acr_pull_role_assignment_cmd)

    webapp_container_set_cmd = [
        "az", "webapp", "config", "container", "set",
        "--name", web_app_name,
        "--resource-group", rg_name,
        "--container-image-name", container_image
    ]
    run_command(webapp_container_set_cmd)

    port_config_cmd = [
        "az", "webapp", "config", "appsettings", "set",
        "--resource-group", rg_name,
        "--name", web_app_name,
        "--settings", f"WEBSITES_PORT={container_port}",
        "--output", "none"
    ]
    run_command(port_config_cmd)

    webapp_restart_cmd = [
        "az", "webapp", "restart",
        "--resource-group", rg_name,
        "--name", web_app_name
    ]
    run_command(webapp_restart_cmd)

    # ==================================
    # Deploy as triggered task (EXTRA)
        # Utilize az acr create task, specifying the dockerfile in github /azure devops repo
        # Setup PAT for the task in Github/Azure Devops repo
        # Create Azure Web App pointing to the image on the ACR and enable Continuous Deployment
        # FLOW: Commit -> ACR build, update image, and send webhook to Azure Web App -> Azure Web App pull new image and restart container


def create_private_dns_zone_if_not_exists(
    rg_name,
    zone_name,
):
    check_zone_cmd = [
        "az", "network", "private-dns", "zone", "list",
        "--resource-group", rg_name,
        "--query", f"[?name=='{zone_name}'].id",
        "--output", "tsv",
    ]

    zone_id = run_command(check_zone_cmd).strip()

    if zone_id:
        print(
            f"Private DNS zone '{zone_name}' already exists "
            f"in resource group '{rg_name}'."
        )
        return zone_id

    create_zone_cmd = [
        "az", "network", "private-dns", "zone", "create",
        "--resource-group", rg_name,
        "--name", zone_name,
        "--query", "id",
        "--output", "tsv",
    ]

    zone_id = run_command(create_zone_cmd).strip()

    print(f"Created private DNS zone '{zone_name}'.")

    return zone_id

def get_private_endpoint_subnet_prefix(
    rg_name,
    vnet_name
):
    
    vnet_show_cmd = [
        "az", "network", "vnet", "show",
        "--resource-group", rg_name,
        "--name", vnet_name,
        "--query", "addressSpace.addressPrefixes[0]",
        "--output", "tsv"
    ]

    vnet_prefix = run_command(vnet_show_cmd).strip()
    vnet_network = ipaddress.ip_network(vnet_prefix)

    if not isinstance(vnet_network, ipaddress.IPv4Network):
        raise ValueError("Only IPv4 VNets are supported.")

    octets = str(vnet_network.network_address).split(".")
    private_endpoint_prefix = (
        f"{octets[0]}.{octets[1]}.10.0/24"
    )

    private_endpoint_network = ipaddress.ip_network(
        private_endpoint_prefix
    )

    if not private_endpoint_network.subnet_of(vnet_network):
        raise ValueError(
            f"{private_endpoint_prefix} is outside "
            f"the VNet address space {vnet_prefix}."
        )

    return private_endpoint_prefix

def create_private_endpoint_for_web_app(   
    rg_name,
    rg_shared_name,
    location,
    vnet_name,
    web_app_name
):
    # Create a subnet for private endpoint
    # Create the private endpoint
    # Create the DNS zone & link it to the vnet
    # Create the DNS zone group between DNS zone and private endpoint to opt-in to Azure's automatic DNS management

    private_endpoint_subnet_name = "private-endpoint-subnet"

    private_endpoint_subnet_prefix = (
        get_private_endpoint_subnet_prefix(
            rg_name,
            vnet_name,
        )
    )

    private_endpoint_name = f"{web_app_name}-pe"
    private_connection_name = f"{web_app_name}-connection"
    private_dns_zone = APP_SERVICE_PRIVATE_DNS_ZONE
    private_dns_link_name = f"{vnet_name}-privatelink"
    dns_zone_group_name = "default"

    create_subnet(
        rg_name,
        vnet_name, 
        private_endpoint_subnet_name, 
        private_endpoint_subnet_prefix
    )
    
    webapp_show_id_cmd = [
        "az", "webapp", "show",
        "--resource-group", rg_name,
        "--name", web_app_name,
        "--query", "id",
        "--output", "tsv"
    ]
    web_app_resource_id = run_command(webapp_show_id_cmd).strip()

    private_endpoint_create_cmd = [
        "az", "network", "private-endpoint", "create",
        "--name", private_endpoint_name,
        "--location", location,
        "--resource-group", rg_name,
        "--vnet-name", vnet_name,
        "--subnet", private_endpoint_subnet_name,
        "--private-connection-resource-id", web_app_resource_id,
        "--group-ids", "sites",
        "--connection-name", private_connection_name
    ]
    run_command(private_endpoint_create_cmd)

    private_dns_zone_id = create_private_dns_zone_if_not_exists(
        rg_shared_name,
        private_dns_zone,
    )

    vnet_show_cmd = [
        "az", "network", "vnet", "show",
        "--resource-group", rg_name,
        "--name", vnet_name,
        "--query", "id",
        "--output", "tsv",
    ]
    vnet_id = run_command(vnet_show_cmd).strip()

    # Needs idempotency check
    private_dns_link_create_cmd = [
        "az", "network", "private-dns", "link", "vnet", "create",
        "--resource-group", rg_shared_name,
        "--zone-name", private_dns_zone,
        "--name", private_dns_link_name,
        "--virtual-network", vnet_id,
        "--registration-enabled", "false",
        "--output", "none",
    ]
    run_command(private_dns_link_create_cmd)

    dns_zone_group_create_cmd = [
        "az", "network", "private-endpoint",
        "dns-zone-group", "create",
        "--resource-group", rg_name,
        "--endpoint-name", private_endpoint_name,
        "--name", dns_zone_group_name,
        "--private-dns-zone", private_dns_zone_id,
        "--zone-name", private_dns_zone,
        "--output", "none",
    ]
    run_command(dns_zone_group_create_cmd)

    disable_public_access_cmd = [
        "az", "resource", "update",
        "--resource-group", rg_name,
        "--name", web_app_name,
        "--resource-type", "Microsoft.Web/sites",
        "--set", "properties.publicNetworkAccess=Disabled"
    ]
    run_command(disable_public_access_cmd)


def create_log_analytics_workspace(
    rg_name,
    workspace_name,
    location,
):
    workspace_show_cmd = [
        "az", "monitor", "log-analytics", "workspace", "show",
        "--resource-group", rg_name,
        "--workspace-name", workspace_name,
        "--output", "none",
    ]

    try:
        run_command(
            workspace_show_cmd,
            display_command=False,
            print_result=False,
        )
        print(f"Log Analytics Workspace '{workspace_name}' already exists.")
        return

    except:
        pass

    workspace_create_cmd = [
        "az", "monitor", "log-analytics", "workspace", "create",
        "--resource-group", rg_name,
        "--workspace-name", workspace_name,
        "--location", location,
        "--sku", LOG_ANALYTICS_SKU,
        "--retention-time", LOG_ANALYTICS_RETENTION_DAYS,
        "--output", "table",
    ]

    run_command(workspace_create_cmd)

def create_azure_managed_grafana():
    # Create grafana workspace
    # Grant 
    pass


# 1. Authenticate the script
# 
# 2. Deploy container app to Azure Web App
# 3. Provision monitoring resources
def start_deployment():
    rg_name = input(f"Resource group [{RG_P1}]: ") or RG_P1
    rg_shared_name = input(f"Resource group [{RG_SHARED}]: ") or RG_SHARED
    location = input(f"Location [{LOCATION}]: ") or LOCATION
    vm_name = input(f"VM name [p1-auth-vm]: ") or "p1-auth-vm"
    acr_name = input(f"ACR name [{ACR_NAME}]: ") or ACR_NAME
    app_service_plan_name = (
        input("App Service Plan [p1-app-service-plan]: ")
        or "p1-app-service-plan"
    )
    app_vnet_name = input("VNet name [p1-vnet]: ") or "p1-vnet" 
    web_app_name = input("Web App name [p1-web-app-test]: ") or "p1-web-app-test"
    image_name = input("Image name [p1-image]: ") or "p1-image"
    image_tag = input("Image tag [latest]: ") or "latest"


    # =========================================

    # 1.
    # For simplicity, authenticate manually with "az login" 
    # authenticate()

    # 2.
    # create_resource_group()
    # create_vnet()
    # create nsg rules for the vnet
    # create and deploy authentication app to vm
    
    create_resource_group(RG_P1)
    create_resource_group(RG_SHARED)

    create_vnet_if_not_exists(RG_P1, app_vnet_name, LOCATION, "10.0.0.0/16")
    create_vm_if_not_exists(rg_name, vm_name, app_vnet_name, LOCATION) 
    deploy_app_to_vm_via_bootstrap_script(rg_name, vm_name, SSH_KEY_PATH, BOOTSTRAP_SCRIPT)   

    deploy_container_to_azure_web_app(
        rg_name=RG_P1,
        acr_name=acr_name,
        location=APP_SERVICE_LOCATION,
        app_service_plan_name=app_service_plan_name,
        web_app_name=web_app_name,
        appservice_sku=APP_SERVICE_SKU,
        image_name=image_name,
        image_tag=image_tag,
        container_port=8081,
        acr_sku=ACR_SKU
    )

    create_private_endpoint_for_web_app(
        rg_name=rg_name,
        rg_shared_name=rg_shared_name,
        location=location,
        vnet_name=app_vnet_name,
        web_app_name=web_app_name
    )

    # 3. 
    create_log_analytics_workspace(
        rg_name=rg_name,
        workspace_name=LOG_ANALYTICS_WORKSPACE_NAME,
        location=location,
    )
    # create_resource_group()
    # create_log_analytics_workspace()
    # create_application_insights(), 
        # for app telemetry, utilize SDK in code  
        # for connection string injection, enable in web app settings
    # configure_diagnostics_settings() (for platform logs)
    # create_azure_managed_grafana()

if __name__ == '__main__':
    start_deployment()
