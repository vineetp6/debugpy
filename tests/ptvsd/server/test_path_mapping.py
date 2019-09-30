# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See LICENSE in the project root
# for license information.

from __future__ import absolute_import, print_function, unicode_literals

import pytest
import sys

from ptvsd.common import fmt
from tests import debug, test_data
from tests.patterns import some


@pytest.mark.skipif(sys.platform == "win32", reason="Linux/Mac only test.")
@pytest.mark.parametrize("os_type", ["INVALID", ""])
def test_client_ide_from_path_mapping_linux_backend(pyfile, target, run, os_type):
    """
    Test simulating that the debug server is on Linux and the IDE is on Windows
    (automatically detect it from the path mapping).
    """

    if os_type == "INVALID":
        # TODO: this test needs to be rewritten after "debugOptions" is removed.
        pytest.skip("https://github.com/microsoft/ptvsd/issues/1770")

    @pyfile
    def code_to_debug():
        from debug_me import backchannel
        import pydevd_file_utils

        backchannel.send(pydevd_file_utils._ide_os)
        print("done")  # @bp

    with debug.Session() as session:
        if os_type == "INVALID":
            session.debug_options |= {fmt("CLIENT_OS_TYPE={0}", os_type)}
        session.config["pathMappings"] = [
            {"localRoot": "C:\\TEMP\\src", "remoteRoot": code_to_debug.dirname}
        ]

        backchannel = session.open_backchannel()
        with run(session, target(code_to_debug)):
            session.set_breakpoints(
                "c:\\temp\\src" + code_to_debug.basename, [code_to_debug.lines["bp"]]
            )

        assert backchannel.receive() == "WINDOWS"
        session.wait_for_stop(
            "breakpoint",
            expected_frames=[
                some.dap.frame(
                    some.dap.source("C:\\TEMP\\src\\" + code_to_debug.basename),
                    line=code_to_debug.lines["bp"],
                )
            ],
        )
        session.request_continue()


def test_with_dot_remote_root(pyfile, long_tmpdir, target, run):
    @pyfile
    def code_to_debug():
        from debug_me import backchannel
        import os

        backchannel.send(os.path.abspath(__file__))
        print("done")  # @bp

    dir_local = long_tmpdir.mkdir("local")
    dir_remote = long_tmpdir.mkdir("remote")

    path_local = dir_local / "code_to_debug.py"
    path_remote = dir_remote / "code_to_debug.py"

    code_to_debug.copy(path_local)
    code_to_debug.copy(path_remote)

    with debug.Session() as session:
        backchannel = session.open_backchannel()
        session.config["pathMappings"] = [{"localRoot": dir_local, "remoteRoot": "."}]

        # Run using remote path
        with run(session, target(path_remote), cwd=dir_remote):
            # Set breakpoints using local path. This ensures that
            # local paths are mapped to remote paths.
            session.set_breakpoints(path_local, all)

        actual_path_remote = backchannel.receive()
        assert some.path(actual_path_remote) == path_remote

        session.wait_for_stop(
            "breakpoint",
            expected_frames=[some.dap.frame(some.dap.source(path_local), line="bp")],
        )

        session.request_continue()


def test_with_path_mappings(pyfile, long_tmpdir, target, run):
    @pyfile
    def code_to_debug():
        from debug_me import backchannel
        import os
        import sys

        backchannel.send(os.path.abspath(__file__))
        call_me_back_dir = backchannel.receive()
        sys.path.insert(0, call_me_back_dir)

        import call_me_back

        def call_func():
            print("break here")  # @bp

        call_me_back.call_me_back(call_func)  # @call_me_back
        print("done")

    dir_local = long_tmpdir.mkdir("local")
    dir_remote = long_tmpdir.mkdir("remote")

    path_local = dir_local / "code_to_debug.py"
    path_remote = dir_remote / "code_to_debug.py"

    code_to_debug.copy(path_local)
    code_to_debug.copy(path_remote)

    call_me_back_dir = test_data / "call_me_back"
    call_me_back_py = call_me_back_dir / "call_me_back.py"

    with debug.Session() as session:
        backchannel = session.open_backchannel()
        session.config["pathMappings"] = [
            {"localRoot": dir_local, "remoteRoot": dir_remote}
        ]

        # Run using remote path
        with run(session, target(path_remote)):
            # Set breakpoints using local path. This ensures that
            # local paths are mapped to remote paths.
            session.set_breakpoints(path_local, ["bp"])

        actual_path_remote = backchannel.receive()
        assert some.path(actual_path_remote) == path_remote
        backchannel.send(call_me_back_dir)

        stop = session.wait_for_stop(
            "breakpoint",
            expected_frames=[
                some.dap.frame(
                    # Mapped files should not have a sourceReference, so that the IDE
                    # doesn't try to fetch them instead of opening the local file.
                    some.dap.source(path_local, sourceReference=0),
                    line="bp",
                ),
                some.dap.frame(
                    # Unmapped files should have a sourceReference, since there's no
                    # local file for the IDE to open.
                    some.dap.source(
                        call_me_back_py, sourceReference=some.int.not_equal_to(0)
                    ),
                    line="callback",
                ),
                some.dap.frame(
                    # Mapped files should not have a sourceReference, so that the IDE
                    # doesn't try to fetch them instead of opening the local file.
                    some.dap.source(path_local, sourceReference=0),
                    line="call_me_back",
                ),
            ],
        )

        srcref = stop.frames[1]["source"]["sourceReference"]

        try:
            session.request("source", {"sourceReference": 0})
        except Exception as ex:
            assert "Source unavailable" in str(ex)
        else:
            pytest.fail("sourceReference=0 should not be valid")

        source = session.request("source", {"sourceReference": srcref})
        assert "def call_me_back(callback):" in source["content"]

        session.request_continue()
