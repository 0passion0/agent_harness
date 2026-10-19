from fastmcp import FastMCP

server = FastMCP("desktop-test")


@server.tool
def echo(value: str) -> str:
    return value


server.run(transport="stdio")
