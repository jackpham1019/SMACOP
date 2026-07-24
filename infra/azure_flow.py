#####################
# This automation script still have idempotence issues, ensure resources from previous runs are properly removed before running to prevent unintended errors

# App Service Plan has throttling behavior for repeated remove/create actions, consider creating a separate persistent rg and reuse it between run
######################
import os
import ipaddress
import json
import tempfile
import shlex
import uuid

from pathlib import Path
from infra.bootstrap_template import get_vm_bootstrap_script
from util import run_command

APP_SERVICE_SKU = 'B1'
APP_SERVICE_LOCATION = "centralus"
APP_SERVICE_PRIVATE_DNS_ZONE = "privatelink.azurewebsites.net"

LOCATION = 'canadaeast'

ACR_SKU = 'Basic'

LOG_ANALYTICS_SKU = "PerGB2018"
LOG_ANALYTICS_RETENTION_DAYS = "30"

MANAGED_IDENTITY_ACR_CONFIG = '{"acrUseManagedIdentityCreds": true}'

PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))
)
WEB_APP_DIR = os.path.join(
    PROJECT_ROOT,
    "azure",
    "web-app"
)
WORKBOOK_TEMPLATE_PATH = os.path.join(
    PROJECT_ROOT,
    "azure",
    "monitoring",
    "SLI_SLO_Dashboard.template.workbook"
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

    check_vm_cmd = [
        "az", "vm", "list", 
        "-g", rg_name,
        "--query", f"[?name=='{vm_name}'].id",
        "-o", "tsv",
    ]
    vm_id = run_command(check_vm_cmd).strip()

    if not vm_id:
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
            "--query", "id",
            "-o", "tsv"
        ]
        vm_id = run_command(create_vm_cmd).strip()

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

    return vm_id

def deploy_app_to_vm_via_bootstrap_script(
        rg_name,
        vm_name,
        bootstrap_script
    ):
    
    bootstrap_vm_cmd = [
        "az", "vm", "run-command", "invoke",
        "--resource-group", rg_name,
        "--name", vm_name,
        "--command-id", "RunShellScript",
        "--scripts", bootstrap_script
    ]
    run_command(bootstrap_vm_cmd)

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
        rg_shared_name,
        location, 
        app_service_plan_name,
        web_app_name, 
        container_image,
        sku=APP_SERVICE_SKU
    ):
    
    create_app_service_plan_if_not_exists(app_service_plan_name, rg_shared_name, location, sku)

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
        rg_shared_name,
        acr_name, 
        location, 
        app_service_plan_name, 
        web_app_name, 
        appservice_sku,
        image_name,
        image_tag,
        container_port,
        acr_sku,
        app_insights_conn_string
    ):
    # Create an ACR
    # Deploy as a quick task
        # Utilize az acr build to let ACR build and push image to the ACR
        # Create Azure Web App pointing to the image on the ACR 


    create_acr_if_not_exists(rg_shared_name, acr_name, acr_sku)
    container_image = build_locally_and_push_image_to_acr(acr_name, image_name, image_tag)

    create_empty_web_app_with_managed_identity(rg_name, rg_shared_name, location, app_service_plan_name, web_app_name, container_image, appservice_sku)

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
        "--assignee", principal_id,
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
        "--settings", 
            f"WEBSITES_PORT={container_port}",
            f"APPLICATIONINSIGHTS_CONNECTION_STRING={app_insights_conn_string}",
        "--output", "none"
    ]
    run_command(port_config_cmd)

    webapp_restart_cmd = [
        "az", "webapp", "restart",
        "--resource-group", rg_name,
        "--name", web_app_name
    ]
    run_command(webapp_restart_cmd)

    get_web_app_endpoint_cmd = [
        "az", "webapp", "show",
        "--name", web_app_name, 
        "--resource-group", rg_name,
        "--query", "defaultHostName",
        "--output", "tsv"
    ]

    return run_command(get_web_app_endpoint_cmd).strip()

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

    workspace_create_cmd = [
        "az", "monitor", "log-analytics", "workspace", "create",
        "--resource-group", rg_name,
        "--workspace-name", workspace_name,
        "--location", location,
        "--sku", LOG_ANALYTICS_SKU,
        "--retention-time", LOG_ANALYTICS_RETENTION_DAYS,
        "--query", "id",
        "-o", "tsv",
    ]
    workspace_id = run_command(workspace_create_cmd).strip()
    return workspace_id

def create_app_insights(
    app_insights_name,
    location,
    rg_name,
    law_name
):

    app_insights_cmd = [
        "az", "monitor", "app-insights", "component", "create",
        "--app", app_insights_name,
        "--location", location,
        "--kind", "web",
        "--resource-group", rg_name,
        "--workspace", law_name,
    ]
    run_command(app_insights_cmd)

    get_conn_string_cmd = [
        "az", "monitor", "app-insights", "component", "show",
        "--app", app_insights_name,
        "--resource-group", rg_name,
        "--query", "connectionString",
        "-o", "tsv"
    ]

    return run_command(get_conn_string_cmd).strip()

def create_dcr(location, law_id, dcr_name, rg_name):
    dcr_definition = {
        "location": location,
        "kind": "Linux",
        "properties": {
            "dataSources": {
                "syslog": [
                    {
                        "name": "syslog",
                        "streams": ["Microsoft-Syslog"],
                        "facilityNames": ["auth", "authpriv"],
                        "logLevels": ["Info"],
                    }
                ]
            },
            "destinations": {
                "logAnalytics": [
                    {
                        "name": "law",
                        "workspaceResourceId": law_id,
                    }
                ]
            },
            "dataFlows": [
                {
                    "streams": ["Microsoft-Syslog"],
                    "destinations": ["law"],
                }
            ],
        },
    }

    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".json",
        delete=False,
        encoding="utf-8",
    ) as file:
        json.dump(dcr_definition, file, indent=2)
        dcr_file_path = file.name

    create_dcr_cmd = [
        "az", "monitor", "data-collection", "rule", "create",
        "--name", dcr_name,
        "--resource-group", rg_name,
        "--location", location,
        "--rule-file", dcr_file_path,
        "--query", "id",
        "--output", "tsv",
    ]

    try:
        dcr_id = run_command(create_dcr_cmd).strip()
    finally:
        Path(dcr_file_path).unlink(missing_ok=True)

    return dcr_id
        
def create_azure_dashboard(subscription_id, rg_name, location, law_id, app_insights_id, workbook_name):

    WORKBOOK_GUID=str(uuid.uuid4())
    workbook_resource_id = (
        f"/subscriptions/{subscription_id}"
        f"/resourceGroups/{rg_name}"
        f"/providers/Microsoft.Insights/workbooks/{WORKBOOK_GUID}"
    )

    workbook_template = Path(WORKBOOK_TEMPLATE_PATH).read_text(encoding="utf-8")
    workbook_template = (
        workbook_template
        .replace("__LOG_ANALYTICS_WORKSPACE_RESOURCE_ID__", law_id)
        .replace("__APPLICATION_INSIGHTS_RESOURCE_ID__", app_insights_id)
        .replace("__WORKBOOK_RESOURCE_ID__", workbook_resource_id)
        .replace("__WORKBOOK_DISPLAY_NAME__", workbook_name)
    )

    workbook_json = json.loads(workbook_template)

    
    workbook_properties = {
        "displayName": workbook_name,
        "serializedData": json.dumps(
            workbook_json,
            separators=(",", ":"),
        ),
        "version": workbook_json.get(
            "version",
            "Notebook/1.0",
        ),
        "sourceId": law_id,
        "category": "workbook",
    }

    workbook_resource = {
        "location": location,
        "kind": "dashboard",
        "properties": workbook_properties
    }

    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".json",
        delete=False,
        encoding="utf-8",
    ) as file:
        json.dump(workbook_resource, file)
        properties_path = Path(file.name)

    create_workbook_cmd = [
        "az", "resource", "create",
        "--resource-group", rg_name,
        "--resource-type", "Microsoft.Insights/workbooks",
        "--name", WORKBOOK_GUID,
        "--location", location,
        "--is-full-object",
        "--properties", f"@{properties_path}",
    ]
    run_command(create_workbook_cmd)



# 1. Authenticate the script
# 2. Provision monitoring resources
# 3. Deploy VM app and container app to Azure Web App
def start_deployment():
    name_suffix = "sqj"

    rg_name = f"p1-rg-{name_suffix}"
    rg_shared_name = f"p1-rg-shared-{name_suffix}"
    location = LOCATION
    vm_name = f"p1-auth-vm-{name_suffix}"
    acr_name = f"p1acr{name_suffix}"
    app_service_plan_name = f"p1-app-service-plan-{name_suffix}"
    app_vnet_name = f"p1-vnet-{name_suffix}"
    web_app_name = f"p1-web-app-{name_suffix}"
    image_name = "p1-image"
    image_tag = "latest"
    dcr_name = f"p1-vm-telemetry-dcr-{name_suffix}"
    app_insights_name = f"p1-app-insights-{name_suffix}"
    law_name = f"p1-log-analytics-{name_suffix}"
    workbook_name = f"p1-workbook-{name_suffix}"

    subscription_id_cmd = [
        "az", "account", "show",
        "--query", "id", 
        "--output", "tsv"
    ]
    subscription_id = run_command(subscription_id_cmd).strip()

    # rg_name = f"p1-rg-test-{name_suffix}"
    # rg_shared_name = f"shared-rg-{name_suffix}"
    # vm_name = f"p1-auth-vm-{name_suffix}"
    # acr_name = f"p1acr{name_suffix}"
    # app_service_plan_name = f"p1-app-service-plan-{name_suffix}"
    # app_vnet_name = f"p1-vnet-{name_suffix}"
    # web_app_name = f"p1-web-app-test-{name_suffix}"
    # image_name = "p1-image"
    # image_tag = "latest"
    # dcr_name = f"p1-vm-telemetry-dcr-{name_suffix}"
    # app_insights_name = f"p1-app-insights-{name_suffix}"
    # =========================================

    # 1.
    # For simplicity, authenticate manually with "az login" 
    # authenticate()

    create_resource_group(rg_name)
    create_resource_group(rg_shared_name)
    create_vnet_if_not_exists(rg_name, app_vnet_name, location, "10.0.0.0/16")


    # 2. 
    # create_resource_group()
    # create_log_analytics_workspace()
    # create_application_insights(), 
        # for app telemetry, utilize SDK in code  
        # for connection string injection, enable in web app settings
    # configure_diagnostics_settings() (for platform logs)
    # create_dashboard()

    law_id = create_log_analytics_workspace(
        rg_name=rg_name,
        workspace_name=law_name,
        location=location,
    )

    app_insights_conn_string = create_app_insights(
        app_insights_name,
        location,
        rg_name,
        law_name
    )

    app_insights_id_cmd = [
        "az", "monitor", "app-insights", "component", "show",
        "--app", app_insights_name,
        "--resource-group", rg_name,
        "--query", "id",
        "--output", "tsv"
    ]
    app_insights_id = run_command(app_insights_id_cmd).strip()

    dcr_id = create_dcr(location, law_id, dcr_name, rg_name)
    
    # 3.
    # deploy web app
    # create and deploy authentication app to vm

    web_app_endpoint = deploy_container_to_azure_web_app(
        rg_name=rg_name,
        rg_shared_name=rg_shared_name,
        acr_name=acr_name,
        location=location,
        app_service_plan_name=app_service_plan_name,
        web_app_name=web_app_name,
        appservice_sku=APP_SERVICE_SKU,
        image_name=image_name,
        image_tag=image_tag,
        container_port=8081,
        acr_sku=ACR_SKU,
        app_insights_conn_string=app_insights_conn_string
    )
    webapp_id_cmd = [
        "az", "webapp", "show", 
        "--resource-group", rg_name,
        "--name", web_app_name,
        "--query", "id",
        "--output", "tsv"
    ]
    web_app_id = run_command(webapp_id_cmd).strip()
    create_diagnostic_setting_cmd = [
        "az", "monitor", "diagnostic-settings", "create",
        "--name", "WebAppDiagnostics",
        "--resource", web_app_id,
        "--workspace", law_id,
        "--logs",
            (
                '[{"category":"AppServiceConsoleLogs","enabled":true},'
                '{"category":"AppServiceHTTPLogs","enabled":true},'
                '{"category":"AppServiceAppLogs","enabled":true},'
                '{"category":"AppServiceAuditLogs","enabled":true}]'
            ),
        "--metrics", '[{"category":"AllMetrics","enabled":true}]',
    ]
    run_command(create_diagnostic_setting_cmd)

    create_private_endpoint_for_web_app(
        rg_name=rg_name,
        rg_shared_name=rg_shared_name,
        location=location,
        vnet_name=app_vnet_name,
        web_app_name=web_app_name
    )

    vm_id = create_vm_if_not_exists(rg_name, vm_name, app_vnet_name, LOCATION)

    create_dcr_association_cmd = [
        "az", "monitor", "data-collection", "rule", "association", "create",
        "--name", "p1-auth-vm-association",
        "--rule-id", dcr_id,
        "--resource", vm_id,
    ]
    run_command(create_dcr_association_cmd)
    install_ama_on_vm_cmd = [
        "az", "vm", "extension", "set",
        "--resource-group", rg_name,
        "--vm-name", vm_name,
        "--name", "AzureMonitorLinuxAgent",
        "--publisher", "Microsoft.Azure.Monitor",
        "--enable-auto-upgrade", "true"
    ]
    run_command(install_ama_on_vm_cmd)

    env_vars = {
        "APPLICATIONINSIGHTS_CONNECTION_STRING": app_insights_conn_string,
        "ACCOUNT_SERVICE_URL": f"http://{web_app_endpoint}"
    }
    env_file_contents = "\n".join(
        f"{k}={shlex.quote(str(v))}"
        for k, v in env_vars.items()
    )
    bootstrap_script = get_vm_bootstrap_script(env_file_contents)
    deploy_app_to_vm_via_bootstrap_script(rg_name, vm_name, bootstrap_script)

    create_azure_dashboard(subscription_id, rg_name, location, law_id, app_insights_id, workbook_name)


if __name__ == '__main__':
    start_deployment()