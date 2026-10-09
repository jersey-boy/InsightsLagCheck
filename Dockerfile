# Container image for the Insights-vs-NationBuilder comparison Lambda.
#
# Uses the official AWS Lambda Python base image so the image is directly
# deployable as a container-image Lambda. pandas/openpyxl have compiled parts,
# which is why a container image is simpler here than a zip + layers.
FROM public.ecr.aws/lambda/python:3.12

# Install Python dependencies into the Lambda task root.
# (boto3 is already present in the base image; installing the pinned versions
# from requirements.txt keeps local and image environments identical.)
COPY requirements.txt ${LAMBDA_TASK_ROOT}/
RUN pip install --no-cache-dir -r ${LAMBDA_TASK_ROOT}/requirements.txt

# Copy the application source. No reference workbook is needed (this pipeline
# compares two live API pulls; it does not use ZIP_Locale_Detail.xlsx).
COPY lambda_pipeline.py ${LAMBDA_TASK_ROOT}/
COPY pull_insights.py ${LAMBDA_TASK_ROOT}/
COPY pull_nationbuilder.py ${LAMBDA_TASK_ROOT}/
COPY compare_pipelines.py ${LAMBDA_TASK_ROOT}/
COPY nb_insights.py ${LAMBDA_TASK_ROOT}/
COPY nationbuilder_v2 ${LAMBDA_TASK_ROOT}/nationbuilder_v2

# Lambda calls <module>.<function>. handler() lives in lambda_pipeline.
CMD ["lambda_pipeline.handler"]
