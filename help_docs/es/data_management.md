
#### Gestión y copia de seguridad de datos

Estos cuatro botones le permiten enriquecer sus datos, hacer una copia de seguridad, transferirlos entre diferentes instancias de la **Nova App** (por ejemplo, mover datos de un ordenador portátil personal a un servidor en la nube) o mantener sincronizadas una instancia local de Nova y su cuenta en el servidor.

**1. Obtener detalles que faltan**

Si tiene objetos en su biblioteca con datos que faltan (como magnitud, tamaño o clasificación), haga clic en este botón.

* **Cómo funciona:** Nova analiza su biblioteca en busca de entradas incompletas y consulta bases de datos astronómicas externas para rellenar automáticamente los espacios en blanco.
* **Nota:** Este proceso puede tardar mucho tiempo, dependiendo del número de objetos que haya que actualizar.

**2. Descargar (copia de seguridad)**

Haga clic en el menú desplegable **Descargar ▼** para exportar sus datos a archivos portátiles. Esto es esencial para hacer una copia de seguridad de su trabajo o migrar a un nuevo dispositivo.

* **Configuración:** exporta sus ubicaciones, objetos y ajustes generales (YAML).
* **Diario:** exporta todos sus proyectos y registros de sesión (YAML).
* **Equipos:** exporta las definiciones de su telescopio, cámara y equipo (YAML).
* **Fotos del diario:** Descarga un archivo ZIP que contiene todas las imágenes adjuntas a sus registros de observación.

**3. Importar (restaurar y transferir)**

Haga clic en el menú desplegable **Importar ▼** para cargar datos desde un archivo de copia de seguridad.

* **Flujo de trabajo:** Seleccione el tipo de datos que desea cargar (Configuración, Diario, Equipos o Fotos) y elija el archivo correspondiente de su ordenador.
* **⚠️ Advertencia importante:** La importación es generalmente una operación de **«Reemplazar»**. Por ejemplo, al importar un archivo de configuración, se reemplazarán sus ubicaciones y objetos actuales por los del archivo. Esto garantiza que su sistema coincida exactamente con el estado de la copia de seguridad, lo que es perfecto para restaurar datos o sincronizar un servidor con su versión local.

**4. Sincronización**

Haga clic en **Sincronización** para mantener sincronizadas esta instancia de Nova y su cuenta en app.nova-tracker.com — objetos, ubicaciones, equipos, proyectos, sesiones del diario, fotos y archivos de registro. Cada ordenador que sincroniza necesita su propio token, de modo que varios ordenadores pueden seguir una misma cuenta en el servidor. En una instalación multiusuario, **Sincronizar con un servidor** solo aparece si un administrador lo ha activado — consulta la ayuda de la página **Sincronización**.

* **Preparación:** En el servidor, haga clic en **Sincronización** y cree un token en **Usar esta instancia de Nova como servidor**. Solo se muestra una vez, así que cópielo de inmediato. En el otro ordenador, haga clic en **Sincronización**, introduzca la **URL del servidor** y el **Token de sincronización** y haga clic en **Conectar**.
* **¿Sus datos están solo en este ordenador por ahora?** Exporte sus datos con **Descargar ▼**, importe esos archivos YAML en el servidor y conéctese solo después: un servidor que no contiene datos rechaza la descarga.
* **Primer paso:** **Descargar del servidor** reemplaza los datos de este ordenador por los del servidor. **Sincronizar ahora** permanece desactivado hasta que esa descarga termine. Si este ordenador ya contiene datos, Nova le pide que escriba REPLACE. Una instancia local de Nova nunca sobrescribe el servidor con todo su conjunto de datos — después de esa primera descarga solo envía sus propios cambios.
* **Sincronizar:** **Sincronizar ahora** envía sus cambios y trae los cambios del servidor. Lea primero el **Resumen de sincronización**: **Subir** y **Bajar**, cada uno con nuevos, modificados y eliminados, además de los archivos. No se aplica nada hasta que haga clic en **Aplicar**, y **Abortar** cancela. Una sincronización que eliminaría una gran parte de los registros de cualquiera de los dos lados pide confirmación dos veces.
* **¿Cambiado en los dos lados?** Gana el cambio más reciente, también cuando una de las partes eliminó el elemento, y en caso de empate gana el servidor. El resumen enumera cada uno de estos elementos y qué copia se conservó: «… — se conserva la versión de este ordenador» o «… — se conserva la versión del servidor».
* **Copias de seguridad:** Nova hace una copia de seguridad de los datos de este ordenador antes de cada descarga y de cada sincronización. Se conservan las 5 copias de sincronización más recientes.
* **Desconectar** elimina el emparejamiento de este ordenador y no borra nada en ninguno de los dos lados. El token sigue siendo válido en el servidor hasta que lo **revoque** allí.
