# Adapted from cloudera/NiFi-MCP-Server (https://github.com/cloudera/NiFi-MCP-Server),
# Copyright Cloudera, Inc., licensed under the Apache License 2.0 (see /LICENSE and /NOTICE).
# Modifications by the nifipilot-plus authors: extracted get_jdbc_driver_troubleshooting() into its own module.

def get_jdbc_driver_troubleshooting() -> str:
    """Get troubleshooting guide for JDBC driver issues."""
    return """
╔══════════════════════════════════════════════════════════════════════════════╗
║           JDBC Driver Requirement for Database Connections                  ║
╚══════════════════════════════════════════════════════════════════════════════╝

Common Error:
  "Driver class com.mysql.cj.jdbc.Driver is not found"
  "ClassNotFoundException: [database].jdbc.Driver"

Root Cause:
  NiFi does not include JDBC drivers by default. They must be installed by your
  NiFi administrator on the NiFi server(s).

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

⚠️  IMPORTANT: This MCP server cannot install JDBC drivers remotely.

JDBC drivers must be installed on the NiFi server by an administrator with
server access. This typically involves:

1. Downloading the appropriate JDBC driver JAR file
2. Copying it to NiFi's lib directory
3. Restarting NiFi

Contact your NiFi administrator to:
  - Install the required JDBC driver for your database
  - Confirm which databases are already supported
  - Get the correct driver class name to use

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

COMMON JDBC DRIVERS NEEDED:

  MySQL/MariaDB:
    Driver Class: com.mysql.cj.jdbc.Driver
    JAR: mysql-connector-j-8.3.0.jar
  
  PostgreSQL:
    Driver Class: org.postgresql.Driver
    JAR: postgresql-42.x.x.jar
  
  Oracle:
    Driver Class: oracle.jdbc.OracleDriver
    JAR: ojdbc8.jar (or ojdbc11.jar)
  
  SQL Server:
    Driver Class: com.microsoft.sqlserver.jdbc.SQLServerDriver
    JAR: mssql-jdbc-12.x.x.jar

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

ALTERNATIVE: Use CDC Processors (No JDBC Driver Needed!)

For real-time data capture, consider using NiFi's CDC processors which don't
require JDBC drivers:

  - MySQL: CaptureChangeMySQL (uses binlog protocol)
  - MongoDB: CaptureChangeMongoDB
  - SQL Server: Use Debezium connectors

These are often more efficient than JDBC-based approaches.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Once drivers are installed by your administrator, you can use this MCP server to:
  ✅ Create and configure DBCPConnectionPool controller services
  ✅ Build complete database integration flows
  ✅ Manage all NiFi operations remotely
"""

