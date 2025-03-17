# Generic Workflow for Sending Objects from Nova to Stellarium via SSH Tunnel

This guide provides a generic workflow for using the **Nova** application hosted on a server (e.g., Raspberry Pi) to remotely control **Stellarium** running locally on your computer (e.g., MacBook). It includes a secure method using SSH tunneling and assumes you're using Nginx as a reverse proxy for secure external access.

### ⚠️ Important Security Consideration

Since your Nova server will be exposed to the internet, anyone knowing your DNS address could access it. To prevent unauthorized access, you must enable a login system.

Nova supports a basic multi-user mode that requires a username and password for access.

### Enable Multi-User Mode in Nova

By default, Nova runs in single-user mode, which allows unrestricted access. To enable login protection:

#### 1. Edit Your Nova Application

Open the `nova.py` file.

  ```bash
  sudo nano nova.py
  ```

Locate this line near the top:
```python
SINGLE_USER_MODE = True  # Set to False for multi-user mode
```
Change it to:
```python
SINGLE_USER_MODE = False
```

#### 2. Set Up User Credentials

Scroll down to the `users` dictionary inside `nova.py` and add your own login credentials. Example:
```python
users = {
    'yourusername': {'id': 'yourid', 'username': 'yourusername', 'password': 'yourpassword'}
}
```
Replace `'yourusername'`, `'yourid'` and `'yourpassword'` with your actual login details.

#### 3. Rename the Default Configuration File

- By default, Nova uses `config_default.yaml`. In **multi-user mode**, each user has their own configuration file.
- Rename `config_default.yaml` to match your **user ID**:

  ```bash
  mv config_default.yaml config_yourid.yaml
  ```

- Nova will now load `config_<user_id>.yaml` based on the logged-in user.

#### 4. Restart the Nova Server

Run:
```sh
python3 nova.py
```
Now, whenever someone visits your Nova page, they must log in first.

#### (Optional) Secure with HTTPS

If you are using NGINX, ensure that HTTPS is enabled to encrypt login credentials.


## Connection to Stellarium Prerequisites

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

