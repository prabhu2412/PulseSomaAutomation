FROM python:3.7.16-slim 



ENV LANG=C.UTF-8 

ENV PYTHONUNBUFFERED=1 



#── openJDK 8 u232  ─────────────────────────── 

# ADD build-assets/openjdk8u232.tar.gz /opt/ 


ADD build-assets/openjdk8u232.tar.gz /opt/ 

RUN set -e;  \
    real_dir=$(find /opt -maxdepth 1 -type d -name "jdk*" -o -name "java*" | head -n 1); \
    echo "JDK found at $real_dir"; \
    ln -s "$real_dir" /opt/jdk8 


# RUN ln -s /opt/jdk*/ /opt/jdk8 

ENV JAVA_HOME=/opt/jdk8 

ENV PATH=$JAVA_HOME/bin:$PATH 



#── jmeter 5.6.3 ───────────────────────────── 

# ADD build-assets/load-apache-jmeter-5.6.3.tgz /opt/ 

# RUN set -e; \
#     find /opt/load-apache-jmeter-5.6.3/bin -type f \( -name "jmeter" -o -name "*.sh" \) -print0 \
#       | xargs -0 -I{} sh -c 'sed -i "s/\r$//" "{}"; chmod +x "{}"'


# ADD build-assets/update-apache-jmeter-5.6.3.tgz /opt/ 

# RUN set -e; \
#     find /opt/update-apache-jmeter-5.6.3/bin -type f \( -name "jmeter" -o -name "*.sh" \) -print0 \
#       | xargs -0 -I{} sh -c 'sed -i "s/\r$//" "{}"; chmod +x "{}"'

# LOAD
ADD build-assets/load-apache-jmeter-5.6.3.tgz /opt/
RUN set -e; \
    src="$(find /opt -maxdepth 1 -type d -name 'apache-jmeter-5.6.3' | head -n1)"; \
    mv "$src" /opt/load-apache-jmeter-5.6.3; \
    find /opt/load-apache-jmeter-5.6.3/bin -type f \( -name 'jmeter' -o -name '*.sh' \) -print0 \
      | xargs -0 -r -I{} sh -c 'sed -i "s/\r$//" "{}"; chmod +x "{}"'
 
# UPDATE
ADD build-assets/update-apache-jmeter-5.6.3.tgz /opt/
RUN set -e; \
    src="$(find /opt -maxdepth 1 -type d -name 'apache-jmeter-5.6.3' | head -n1)"; \
    mv "$src" /opt/update-apache-jmeter-5.6.3; \
    find /opt/update-apache-jmeter-5.6.3/bin -type f \( -name 'jmeter' -o -name '*.sh' \) -print0 \
      | xargs -0 -r -I{} sh -c 'sed -i "s/\r$//" "{}"; chmod +x "{}"'


# ENV PATH=/opt/apache-jmeter-5.6.3/bin:$PATH 



#── busyBox for pkill / pgrep  ──────────────── 

ADD build-assets/busybox /bin/busybox 

RUN chmod +x /bin/busybox && ln -s /bin/busybox /bin/pkill && ln -s /bin/busybox /bin/pgrep 



WORKDIR /app 



#── python wheels  ──────────────────────────── 

COPY wheels/ /wheels/ 

RUN pip install --no-index --find-links=/wheels /wheels/* 



#── application code & runtime folders ──────────────── 

COPY backend/ /app/backend/ 

COPY scripts/ /app/scripts/ 

COPY runner/ /app/runner/ 

RUN chmod +x /app/runner/debug_update.sh

RUN chmod +x /app/scripts/soma_bash.sh 

RUN chmod +x /app/scripts/impulse_bash.sh


                 
ADD testplans/SOMA_FIPUpdates_Adjustments_MqMay2025.jmx /opt/update-apache-jmeter-5.6.3/ 

ADD testplans/KAFKA_OrderLoad_10KMay2025.jmx /opt/load-apache-jmeter-5.6.3/ 

ADD testplans/KAFKA_OrderLoad_40KMay2025.jmx /opt/load-apache-jmeter-5.6.3/

#── pre-built React static assets ───────────────────── 

COPY frontend/dist/ /app/static/ 



EXPOSE 80 

CMD ["uvicorn", "backend.main:asgi_app", "--host", "0.0.0.0", "--port", "80"] 

# CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "80"] 
