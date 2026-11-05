ARG BASE_IMAGE=room-litereality-agent:0.1.3-cuda124-r4
FROM ${BASE_IMAGE}
COPY sshd-bootstrap.sh install_patch.py /opt/room/ssh-platform-r5/
RUN python3.10 /opt/room/ssh-platform-r5/install_patch.py
LABEL org.room.build.variant="cuda124-r5" \
      org.room.ssh.fix="direct-sshd-bootstrap-and-service-process-matching"
