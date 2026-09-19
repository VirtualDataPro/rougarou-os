# Rougarou interactive login shell. Noninteractive SSH/scp remains quiet.
case $- in
  *i*)
    if [ -n "${BASH_VERSION:-}" ] && [ -r /usr/share/rougarou/bashrc ]; then
      . /usr/share/rougarou/bashrc
    fi
    if [ "$(id -u)" -ne 0 ] && command -v rougarou >/dev/null 2>&1; then
      if [ -t 0 ] && [ -t 1 ] && [ -z "${ROUGAROU_WELCOME_SHOWN:-}" ]; then
        export ROUGAROU_WELCOME_SHOWN=1
        rougarou welcome
        rougarou setup --first-login
      fi
    fi
    ;;
esac
