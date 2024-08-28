FROM quay.io/operate-first/opf-toolbox:v0.8.0

# Create the destination directory
WORKDIR /scripts

# Install requirements first to maximize caching
COPY ./requirements.txt ./requirements.txt
RUN pip install -r requirements.txt

# Install the group-sync application
COPY ./group-sync.py ./
