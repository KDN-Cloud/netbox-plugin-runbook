docker stop netbox-dt-import
docker rm netbox-dt-import

docker run -d \
  --network netbox_default \
  --name netbox-dt-import \
  -e NETBOX_URL=http://netbox:8000 \
  -e NETBOX_TOKEN=nxMj39fPah4LrrUi1I6czdXD696P8gML2y8iHL57 \
  -e REPO_URL=https://github.com/netbox-community/devicetype-library.git \
  ghcr.io/marcinpsk/device-type-library-import:latest \
  python nb-dt-import.py --vendors 'Apple,APC,ASUS,Cable Matters,CyberPower,Intel,Lenovo,Netgate,NVIDIA,Raspberry Pi Foundation,Seagate,Server Technology,Synology,TP-Link,Ubiquiti,WatchGuard' --update

