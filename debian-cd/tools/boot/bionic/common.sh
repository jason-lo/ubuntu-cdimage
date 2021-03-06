
# This file provides some common code that is intented to be called
# by the various boot-<arch> scripts.


# install_languages decompacts the language packs, you should give the path
# to the CD temporary tree.
# This function should be called for all bootable images.
install_languages() {
    # Param $1 is the CD directory
    if [ -f "$MIRROR/dists/$DI_CODENAME/main/disks-$ARCH/current/xlp.tgz" ]
    then
	mkdir $1/.xlp
	(cd $1/.xlp; \
	tar zxf $MIRROR/dists/$DI_CODENAME/main/disks-$ARCH/current/xlp.tgz )
    fi
}

default_preseed() {
    case $PROJECT in
	ubuntu|ubuntukylin)
	    DEFAULT_PRESEED='file=/cdrom/preseed/ubuntu.seed'
	    ;;
	kubuntu)
	    DEFAULT_PRESEED='file=/cdrom/preseed/kubuntu.seed'
	    ;;
	kubuntu-plasma5)
	    DEFAULT_PRESEED='file=/cdrom/preseed/kubuntu-plasma5.seed'
	    ;;
	edubuntu)
	    DEFAULT_PRESEED='file=/cdrom/preseed/edubuntu.seed'
	    ;;
	xubuntu)
	    DEFAULT_PRESEED='file=/cdrom/preseed/xubuntu.seed'
	    ;;
	ubuntu-server)
	    if [ "$CDIMAGE_LIVE" != 1 ]; then
		DEFAULT_PRESEED='file=/cdrom/preseed/ubuntu-server.seed'
	    fi
	    ;;
	ubuntu-mid)
	    DEFAULT_PRESEED='file=/cdrom/preseed/mid.seed'
	    ;;
	ubuntu-netbook)
	    DEFAULT_PRESEED='file=/cdrom/preseed/ubuntu-netbook.seed'
	    ;;
	jeos)
	    DEFAULT_PRESEED='file=/cdrom/preseed/jeos.seed'
	    ;;
	ubuntustudio)
	    DEFAULT_PRESEED='file=/cdrom/preseed/ubuntustudio.seed'
	    ;;
	mythbuntu)
	    DEFAULT_PRESEED='file=/cdrom/preseed/mythbuntu.seed'
	    ;;
	lubuntu)
	    DEFAULT_PRESEED='file=/cdrom/preseed/lubuntu.seed'
	    ;;
	ubuntu-gnome)
	    DEFAULT_PRESEED='file=/cdrom/preseed/ubuntu-gnome.seed'
	    ;;
	ubuntu-mate)
	    DEFAULT_PRESEED='file=/cdrom/preseed/ubuntu-mate.seed'
	    ;;
	ubuntu-budgie)
	    DEFAULT_PRESEED='file=/cdrom/preseed/ubuntu-budgie.seed'
	    ;;
	*)
	    DEFAULT_PRESEED=
	    ;;
    esac
    if [ "$CDIMAGE_INSTALL_BASE" = 1 ]; then
	case $PROJECT in
	    ubuntu|ubuntu-*)
		;;
	    *)
		FRONTEND_BACKGROUND="FRONTEND_BACKGROUND=original"
		DEFAULT_PRESEED="${DEFAULT_PRESEED:+$DEFAULT_PRESEED }$FRONTEND_BACKGROUND"
		;;
	esac
    fi
}

default_language() {
    case $PROJECT in
	ubuntukylin)
	    KERNEL_PARAMS="${KERNEL_PARAMS:+$KERNEL_PARAMS }locale=zh_CN keyboard-configuration/layoutcode?=cn"
	    ;;
    esac
}

list_kernel_abis() {
    perl -le '
	BEGIN { %images = map { $_ => 1 } @ARGV; $found = 0; %abis = (); }
	while (<STDIN>) {
	    chomp;
	    if (/^[^[:space:]]/) {
		$found = exists $images{$_};
	    } elsif ($found and /^[[:space:]]+kernel-image-([^[:space:]]*)-di /) {
		$abis{$1} = 1;
	    }
	}
	END { print for sort keys %abis; }' "$@" <MANIFEST.udebs
}

check_kernel_sync() {
    [ "$CDIMAGE_INSTALL_BASE" = 1 ] || return 0
    for abi in $(sed -n 's/^kernel-image-\([^ ]*\)-di .*/\1/p'); do
	# If any parameters were passed, then they represent a list of ABI
	# suffixes we're interested in.
	if [ "$#" -gt 0 ]; then
	    local allowed=
	    for allow_abi; do
		case $abi in
		    *-$allow_abi)
			allowed=1
			break
			;;
		esac
	    done
	    if [ -z "$allowed" ]; then
		continue
	    fi
	fi
	if ! grep -q -- "-$abi-di\$" list; then
	    echo "debian-installer has kernel ABI $abi, but no corresponding udebs are on the CD!" >&2
	    exit 1
	fi
    done
}

initrd_suffix() {
    if zcat -t "$1" >/dev/null 2>&1; then
	echo .gz
    elif bzcat -t "$1" >/dev/null 2>&1; then
	echo .bz2
    # TODO: This only works if there is at least one dot in $1; fortunately
    # this is currently always true.
    elif lzcat -S ".${1##*.}" -t "$1" >/dev/null 2>&1; then
	# .lzma would be more conventional, but we use .lz to avoid creating
	# trouble for boot loaders that might need to read from 8.3
	# filesystems without implementing support for long file names (e.g.
	# syslinux on FAT USB sticks).
	echo .lz
    fi
}

HUMANPROJECT="$(echo "$CAPPROJECT" | sed 's/-/ /g')"

# Add an option to the mkisofs options for this CD _only_ if it's not
# already set. $1 is the opts file location, "$2" is the new
# option. Call this with _logical groupings_ of options
add_mkisofs_opt() {
   OPTS_FILE=$1
   NEW_OPT="$2"

   if ! ( grep -q -- "$NEW_OPT" $OPTS_FILE 2>/dev/null) ; then
       echo -n "$NEW_OPT " >> $OPTS_FILE
   fi
}
