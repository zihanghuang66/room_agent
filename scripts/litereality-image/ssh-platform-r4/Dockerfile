ARG BASE_IMAGE=room-litereality-agent:0.1.3-cuda124-r3
FROM ${BASE_IMAGE}
COPY entrypoint.sh sshd.sh ssh-prepare.sh sshd_config install_patch.py /opt/room/ssh-platform-r4/
RUN python3.10 /opt/room/ssh-platform-r4/install_patch.py
WORKDIR /workspace
LABEL org.room.build.variant="cuda124-r4" \
      org.room.ssh.fix="independent-startup-system-config-platform-service"
EXPOSE 22
ENTRYPOINT ["/usr/bin/tini", "--", "/opt/room/image/entrypoint.sh"]
CMD ["/usr/local/bin/start-ssh.sh"]
