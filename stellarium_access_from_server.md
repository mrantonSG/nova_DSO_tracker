# Generic Workflow for Sending Objects from Nova to Stellarium via SSH Tunnel

This guide provides a generic workflow for using the **Nova** application hosted on a server (e.g., Raspberry Pi) to remotely control **Stellarium** running locally on your computer (e.g., MacBook). It includes a secure method using SSH tunneling and assumes you're using Nginx as a reverse proxy for secure external access.

## Prerequisites

- **Nova** running on your server.
- **Stellarium** installed and running on your local machine.
- Server reachable via secure HTTPS (e.g., using DuckDNS and Nginx).
- SSH access to your server (commonly Raspberry Pi).

## Step-by-step Instructions

### 1. Prepare the Application on the Server
Ensure the latest version of Nova is installed and accessible via your server, and that the necessary HTML interface files are correctly deployed.

### 2. Configure Stellarium on Your Local Machine
- Launch **Stellarium** on your computer.
- Go to:
  - **Configuration Window (F2)** → **Plugins**.
- Enable the **RemoteControl** plugin.
- Verify the plugin listens on port `8090` (default).

### 2. Establish an SSH Reverse Tunnel
Create a secure connection from your local machine (with Stellarium) to your remote server to enable the application to communicate back to Stellarium:

```bash
ssh -R 8090:localhost:8090 your_user@your_server_hostname -p <server-ssh-port>
```

Replace the following placeholders:
- `your-server`: domain name (e.g., `yourname.duckdns.org`) or IP address of your server.
- `<ssh-port>`: SSH port for your server (usually `22`, unless changed).
- `user`: Your username for the SSH connection.

#### Example:

```bash
ssh -R 8090:localhost:8090 pi@mynova.duckdns.org -p 2222
```

#### Explanation:
- The SSH command establishes a reverse tunnel forwarding port `8090` from the server back to port `8090` on your local machine.
- This makes Stellarium's RemoteControl accessible remotely via your server.

### 3. Verify the Connection
- Connect to your application's web interface hosted on your server.
- When selecting an object and clicking the button to send it to Stellarium, the local Stellarium instance should automatically focus on this object.

## Notes
- The SSH reverse tunnel needs to be re-established each time your local computer restarts or the SSH connection is dropped.
- You can automate the tunnel setup using tools like `autossh` or creating an SSH script to run automatically on startup.

This workflow allows remote, secure, and seamless integration between your astronomical application hosted remotely and your local Stellarium software.

