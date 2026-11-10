### Sincronización de instancias

Nova puede sincronizar tus datos entre dos instalaciones: una actúa como
**servidor** (el hub) y la otra se conecta como **cliente**. Así mantienes al
día un equipo de casa y otro del observatorio.

#### Configurar el servidor

1. En el equipo que guardará tus datos, abre **Configuración → Sync**.
2. En **Usar este Nova como servidor**, crea un token para cada instancia que se
   conectará. Copia el token de inmediato: solo se guarda un hash, por lo que
   nunca podrá volver a mostrarse.

**Ruta local (YAML).** Un servidor rechaza la descarga mientras solo tenga sus
datos iniciales. Si vas a mover una instalación existente al servidor, importa
allí primero tus archivos YAML (Configuración → Herramientas de datos →
Importar). Eso es lo que permite la primera descarga.

#### Conectar un cliente

1. En el otro equipo, abre **Configuración → Sync** y ve a **Sincronizar con un
   servidor**.
2. Introduce la URL del servidor (por ejemplo `https://app.nova-tracker.com`) y
   el token que creaste allí, y pulsa **Conectar**. Se muestra el nombre de la
   cuenta del servidor y el token se guarda en este equipo.

#### Descargar del servidor

La primera sincronización de cada emparejamiento es una **descarga**: sustituye
los datos de este equipo por los del servidor. Si este equipo ya tiene datos,
debes escribir `REPLACE` para confirmar — los archivos sustituidos se apartan,
nunca se eliminan. **Descargar del servidor** restablece este equipo al estado
del servidor en cualquier momento.

#### Sincronizar ahora

Una vez completada una descarga, **Sincronizar ahora** envía tus cambios y trae
los del servidor. Se muestra un resumen antes de aplicar nada; revísalo y elige
**Aplicar** o **Abortar**. Una sincronización que eliminaría gran parte de tus
datos siempre pide una segunda confirmación.

#### Desconectar

**Desconectar** borra el emparejamiento y el token guardado de este equipo.

#### Copias de seguridad y restauración

Nova hace una copia local antes de cada descarga y sincronización, en
`instance/backups/sync/`. Para restaurar: detén la app, copia la `app.db`
respaldada sobre `instance/app.db`, copia las carpetas `uploads/` y `logs/`
respaldadas de vuelta a `instance/` y reinicia la app.
