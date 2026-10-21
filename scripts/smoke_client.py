"""
Smoke-test a running OpenFlexure Microscope connector over SiLA 2 / gRPC.

Usage:
    uv run python scripts/smoke_client.py --port <sila port> [--host 127.0.0.1] [--move-z 20] [--save preview.jpg]

Reads the server identity, lists the features, prints the stage position, grabs a preview frame (saved to a file) and,
with --move-z, moves the stage up by that many steps and back down again.
"""

import argparse
import asyncio
import pathlib

from sila.client import Client, ClientConfig

FQ = "ca.accelerationconsortium/microscopy/{}/v1"


async def wait_for_responses(execution: object) -> dict:
    """
    Wait until an observable command execution has finished, then return its responses.

    `get_responses()` does not wait: called while the command still runs it raises `CommandExecutionNotFinished`.
    The status stream ends with the final status, so consuming it is the wait.
    """
    async for _status in await execution.subscribe_status():
        pass
    return await execution.get_responses()


async def main(host: str, port: int, move_z: int, save: pathlib.Path) -> None:
    config = ClientConfig(hostname=host, port=port, tls=False, default_timeout=30_000)
    async with Client(config) as client:
        service = await client.load_feature("org.silastandard/core/SiLAService/v1")
        print("Server:", await service.properties["ServerName"].read())
        print("Features:")
        for identifier in await service.properties["ImplementedFeatures"].read():
            print("  -", identifier)

        simulation = await client.load_feature("org.silastandard/core/SimulationController/v1")
        print("SimulationMode:", await simulation.properties["SimulationMode"].read())

        provider = await client.load_feature(FQ.format("MicroscopeProvider"))
        print("Microscope:", await provider.properties["MicroscopeName"].read())
        print("Server version:", await provider.properties["ServerVersion"].read())
        print("API base URL:", await provider.properties["APIBaseURL"].read())
        print("Camera type:", await provider.properties["CameraType"].read())

        stage = await client.load_feature(FQ.format("StageController"))
        print("Position:", await stage.properties["Position"].read())
        print("Stage type:", await stage.properties["StageType"].read())

        acquisition = await client.load_feature(FQ.format("ImageAcquisitionController"))
        frame = (await acquisition.commands["GrabPreviewFrame"].execute({}))["Image"]
        await asyncio.to_thread(save.write_bytes, frame)
        print(f"Preview frame: {len(frame)} bytes -> {save}")

        if move_z:
            print(f"MoveBy z=+{move_z} ...")
            execution = await stage.commands["MoveBy"].execute({"X": 0, "Y": 0, "Z": move_z})
            print("  ->", await wait_for_responses(execution))
            print(f"MoveBy z=-{move_z} ...")
            execution = await stage.commands["MoveBy"].execute({"X": 0, "Y": 0, "Z": -move_z})
            print("  ->", await wait_for_responses(execution))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default="127.0.0.1", help="Host the connector's SiLA server listens on.")
    parser.add_argument("--port", type=int, required=True, help="Port the connector's SiLA server listens on.")
    parser.add_argument("--move-z", type=int, default=0, help="Move the stage up by this many steps and back down.")
    parser.add_argument(
        "--save", type=pathlib.Path, default=pathlib.Path("preview.jpg"), help="Where to save the frame."
    )
    args = parser.parse_args()
    asyncio.run(main(args.host, args.port, args.move_z, args.save))
